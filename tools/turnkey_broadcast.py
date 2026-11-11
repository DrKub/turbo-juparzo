"""Broadcast one Turnkey-signed swap. SEPARATE from signing, and off unless BOTH gates are open:

  1. config/turnkey/signer.json has "broadcast": true (default false), AND
  2. the caller passes confirm=True (CLI: --broadcast-confirm).

This is the only module in the repo that can reach Solana's sendTransaction; tools/readonly_rpc.py keeps
refusing it for everything else. One send per ticket, never retried: the O_EXCL marker
data/turnkey-signer/broadcast/<ticket_id> is created BEFORE the RPC call and is never removed.

Checks before sending (any failure -> BroadcastRefused, nothing sent):
  kill switch absent; signer enabled; ticket issued by the sign gate and signed through the signer
  (data/turnkey-signer/used/<ticket_id>); signed file parses, carries the ticket's message hash, the fee
  payer is the turnkey wallet and its ed25519 signature verifies; the local policy mirror still matches;
  the blockhash is still valid with >= MIN_BLOCKS_LEFT blocks left (read-only RPC).
After sending, the returned signature must equal the one inside the signed transaction; it is logged as a
`note` (event broadcast). Confirm on chain with tools/record_result.py SIGNATURE --ticket TICKET.json.
"""

import base64
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from metis_offline.base58 import b58encode  # noqa: E402
from metis_offline.wire import WireError, parse_transaction  # noqa: E402
from tools import oplog, sign_gate, turnkey_policy_mirror as mirror, turnkey_signer as ts, wallet  # noqa: E402
from tools.bridge import STATE_DIR as BRIDGE_STATE_DIR  # noqa: E402
from tools.readonly_rpc import RPC, http_json, rpc as read_rpc  # noqa: E402

MIN_BLOCKS_LEFT = 5
STATE_DIR = ts.STATE_DIR


class BroadcastRefused(Exception):
    def __init__(self, reason: str, **facts):
        super().__init__(reason)
        self.reason, self.facts = reason, facts


def send_raw_transaction(wire_b64: str, url: str = RPC) -> str:
    """The one sendTransaction call in the repo. No preflight skip, no node-side retries."""
    out = http_json(url, {"jsonrpc": "2.0", "id": 1, "method": "sendTransaction",
                          "params": [wire_b64, {"encoding": "base64", "skipPreflight": False, "preflightCommitment": "confirmed", "maxRetries": 0}]})
    if "error" in out:
        raise RuntimeError(f"sendTransaction: {out['error']}")
    return out["result"]


def broadcast(signed_path: Path, ticket_path: Path, *, confirm: bool, config_path: Path = ts.CONFIG_PATH, kill_file: Path = BRIDGE_STATE_DIR / "KILL",
              state_dir: Path = STATE_DIR, gate_state_dir: Path = sign_gate.STATE_DIR, wallet_path: Path = wallet.CONFIG_PATH,
              rpc=read_rpc, send=send_raw_transaction, log_path: Path | None = None, sig_verify=ts._ed25519_ok) -> dict:
    try:
        cfg = ts.load_config(config_path)
    except ts.ConfigError as exc:
        raise BroadcastRefused(f"config:{exc}")
    if cfg.get("broadcast") is not True:
        raise BroadcastRefused("broadcast_disabled_in_config")
    if confirm is not True:
        raise BroadcastRefused("broadcast_confirm_missing")
    if cfg.get("enabled") is not True:
        raise BroadcastRefused("signer_disabled")
    if Path(kill_file).exists():
        raise BroadcastRefused("kill_switch")
    try:
        ticket = json.loads(Path(ticket_path).read_text(encoding="utf-8"))
        sha = ticket["message_sha256"]
        issued = json.loads((Path(gate_state_dir) / "consumed" / sha).read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, TypeError):
        raise BroadcastRefused("ticket_not_issued_by_gate")
    for key in ("ticket_id", "message_sha256", "unsigned_sha256", "blockhash", "last_valid_block_height"):
        if issued.get(key) != ticket.get(key):
            raise BroadcastRefused("ticket_mismatch", field=key)
    if not (Path(state_dir) / "used" / str(ticket["ticket_id"])).is_file():
        raise BroadcastRefused("ticket_not_signed_by_turnkey_signer")
    profile = wallet.load_profile("turnkey", path=wallet_path)
    try:
        signed_raw = bytes.fromhex(Path(signed_path).read_text(encoding="ascii").strip())
        signed = parse_transaction(signed_raw)
    except (OSError, ValueError, WireError):
        raise BroadcastRefused("signed_unreadable")
    if hashlib.sha256(signed.message).hexdigest() != sha:
        raise BroadcastRefused("signed_message_mismatch")
    if not signed.account_keys or b58encode(signed.account_keys[0]) != profile.address:
        raise BroadcastRefused("fee_payer_mismatch")
    if not signed.signatures or not sig_verify(signed.account_keys[0], signed.signatures[0], signed.message):
        raise BroadcastRefused("signed_signature_invalid")
    mismatches = mirror.swap_failures(signed, profile.address, profile.wsol_ata, profile.usdc_ata)
    if mismatches:
        raise BroadcastRefused("local_policy_mismatch", mismatches=mismatches)
    try:
        height = rpc("getBlockHeight", [{"commitment": "confirmed"}])
        valid = rpc("isBlockhashValid", [ticket["blockhash"], {"commitment": "confirmed"}])["value"]
    except Exception as exc:  # noqa: BLE001 - cannot prove freshness -> do not send
        raise BroadcastRefused("freshness_unknown", error_type=type(exc).__name__)
    left = int(ticket["last_valid_block_height"]) - int(height)
    if valid is not True or left < MIN_BLOCKS_LEFT:
        raise BroadcastRefused("blockhash_stale", blocks_left=left, blockhash_valid=valid)
    marker_dir = Path(state_dir) / "broadcast"
    marker_dir.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(marker_dir / str(ticket["ticket_id"]), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        raise BroadcastRefused("ticket_already_broadcast")
    os.write(fd, json.dumps({"message_sha256": sha}).encode())
    os.close(fd)
    expected = b58encode(signed.signatures[0])
    returned = send(base64.b64encode(signed_raw).decode("ascii"))  # one attempt, exceptions propagate (no retry)
    oplog.append("note", {"component": "turnkey_broadcast", "event": "broadcast", "ticket_id": ticket["ticket_id"], "signature": returned,
                          "signature_matches_signed_tx": returned == expected, "blocks_left": left}, sha, path=log_path)
    return {"status": "broadcast_sent", "signature": returned, "signature_matches_signed_tx": returned == expected, "ticket_id": ticket["ticket_id"],
            "message_sha256": sha, "next": f"python3 tools/record_result.py {returned} --ticket {ticket_path} --signer {profile.address} --mechanism 'turnkey'"}
