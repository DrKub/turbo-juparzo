"""Fail-closed sign gate. It never signs or sends; it only decides.

The signer (Phantom embedded wallet) is driven outside this repo. The rule is:
sign/send only immediately after a successful semantic check AND a successful
read-only simulation of the exact same message. This gate re-verifies all of
that from the build directory and issues a short-lived, single-use ticket.

Rules, checked in this order (any failure -> denied, exit 2, logged):
 0. Optional expected_user (wallet profile): request intent user and fee payer
    must both equal it (wallet_mismatch).
 1. summary.json, unsigned.b64 and metis-request.json exist and parse
    (build_files_missing / unsigned_unparseable / request_unparseable).
 2. metis-request.json carries exactly the bytes in unsigned.b64 (request_wire_mismatch).
 3. Saved metis result: exit 0, kind semantic_pass, reason ok_unsigned,
    trade_authorized false (metis_not_passed).
 4. message_sha256 recomputed from unsigned.b64 == saved check message_sha256
    (message_sha_mismatch_check) == simulation.message_sha256
    (message_sha_mismatch_simulation): the simulation ran on the same message.
 5. Simulation err null (simulation_error); no persistent account created and
    WSOL ATA gone after (simulation_accounts); USDC raw delta >= the check's
    min_out_amount_floor (simulation_output_below_min).
 6. Summary age (now - finished_at_epoch) <= MAX_AGE_S = 30 s (summary_too_old).
 7. last_valid_block_height in the request == the build's (last_valid_mismatch).
 8. Fresh read-only getBlockHeight: last_valid - height >= MIN_BLOCKS = 45
    (block_margin_low).
 9. The preflight is re-run in-process with that fresh height and must pass
    again on the same message (preflight_rerun_failed).
10. isBlockhashValid is true (blockhash_invalid).
11. Single use: data/sign-gate/consumed/<message_sha256> is created with
    O_EXCL; if it already exists -> ticket_reused.
Approved -> ticket JSON (ticket_id, message_sha256, unsigned_sha256, blockhash,
last_valid_block_height, blocks_left, issued_at, expires_at = +TICKET_TTL_S)
printed and saved, exit 0.
"""

import base64
import binascii
import hashlib
import json
import os
import secrets
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from metis_offline.cli import _parse_request  # noqa: E402
from metis_offline.preflight import Evidence, Freshness, preflight  # noqa: E402
from metis_offline.wire import WireError, parse_transaction  # noqa: E402
from tools import oplog  # noqa: E402
from tools.readonly_rpc import rpc as default_rpc  # noqa: E402

MAX_AGE_S = 30
MIN_BLOCKS = 45
TICKET_TTL_S = 20
STATE_DIR = ROOT / "data" / "sign-gate"


class Denied(Exception):
    def __init__(self, reason: str, **facts):
        super().__init__(reason)
        self.reason = reason
        self.facts = facts


def _load(build_dir: Path):
    try:
        summary = json.loads((build_dir / "summary.json").read_text(encoding="utf-8"))
        wire_b64 = (build_dir / "unsigned.b64").read_text(encoding="utf-8").strip()
        request_text = (build_dir / "metis-request.json").read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError):
        raise Denied("build_files_missing")
    try:
        raw = base64.b64decode(wire_b64, validate=True)
        tx = parse_transaction(raw)
    except (binascii.Error, ValueError, WireError):
        raise Denied("unsigned_unparseable")
    try:
        req_wire, intent, evidence = _parse_request(request_text)
    except ValueError:
        raise Denied("request_unparseable")
    if req_wire != wire_b64 or intent is None:
        raise Denied("request_wire_mismatch")
    return summary, wire_b64, raw, tx, intent, evidence


def evaluate(build_dir: Path, *, rpc=default_rpc, now=time.time, state_dir: Path = STATE_DIR,
             max_age_s: int = MAX_AGE_S, min_blocks: int = MIN_BLOCKS, expected_user: str | None = None) -> dict:
    """Return a ticket dict or raise Denied. Consumes the message on approval."""
    summary, wire_b64, raw, tx, intent, evidence = _load(Path(build_dir))
    message_sha = hashlib.sha256(tx.message).hexdigest()
    if expected_user is not None:
        from metis_offline.base58 import pubkey as _pubkey

        if intent.user != _pubkey(expected_user) or tx.account_keys[0] != intent.user:
            raise Denied("wallet_mismatch", message_sha256=message_sha)
    metis = summary.get("metis_offline") or {}
    report = metis.get("report") or {}
    if metis.get("exit") != 0 or report.get("kind") != "semantic_pass" or report.get("reason") != "ok_unsigned" or report.get("trade_authorized") is not False:
        raise Denied("metis_not_passed", metis_exit=metis.get("exit"), metis_reason=report.get("reason"))
    sim = summary.get("simulation") or {}
    if report.get("message_sha256") != message_sha:
        raise Denied("message_sha_mismatch_check", message_sha256=message_sha)
    if sim.get("message_sha256") != message_sha:
        raise Denied("message_sha_mismatch_simulation", message_sha256=message_sha)
    if "err" not in sim or sim.get("err") is not None:
        raise Denied("simulation_error", sim_err=sim.get("err", "missing"))
    if sim.get("persistent_accounts_created") != [] or sim.get("wsol_ata_exists_after") is not False:
        raise Denied("simulation_accounts")
    min_out = ((report.get("details") or {}).get("swap") or {}).get("min_out_amount_floor")
    delta = sim.get("usdc_raw_delta")
    if type(min_out) is not int or type(delta) is not int or delta < min_out:
        raise Denied("simulation_output_below_min", usdc_raw_delta=delta, min_out=min_out)
    finished = summary.get("finished_at_epoch")
    t = now()
    if not isinstance(finished, (int, float)) or t - finished > max_age_s or t < finished - 5:
        raise Denied("summary_too_old", age_s=None if not isinstance(finished, (int, float)) else round(t - finished, 1))
    last_valid = (evidence.freshness.last_valid_block_height if evidence.freshness else None)
    if last_valid is None or last_valid != (summary.get("build") or {}).get("last_valid_block_height"):
        raise Denied("last_valid_mismatch")
    height = rpc("getBlockHeight", [{"commitment": "confirmed"}])
    blocks_left = last_valid - height
    if blocks_left < min_blocks:
        raise Denied("block_margin_low", blocks_left=blocks_left, min_blocks=min_blocks)
    fresh_evidence = Evidence(evidence.tables, Freshness(evidence.freshness.blockhash, last_valid, height))
    rerun = preflight(raw, intent, fresh_evidence)
    if not rerun.semantic_pass or rerun.reason != "ok_unsigned" or rerun.message_sha256 != message_sha:
        raise Denied("preflight_rerun_failed", rerun_reason=rerun.reason)
    from metis_offline.base58 import b58encode

    blockhash = b58encode(tx.recent_blockhash)
    valid = rpc("isBlockhashValid", [blockhash, {"commitment": "confirmed"}])
    if not (isinstance(valid, dict) and valid.get("value") is True):
        raise Denied("blockhash_invalid")
    consumed = Path(state_dir) / "consumed"
    consumed.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(consumed / message_sha, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        raise Denied("ticket_reused", message_sha256=message_sha)
    issued = now()
    ticket = {
        "ticket_id": secrets.token_hex(8),
        "message_sha256": message_sha,
        "unsigned_sha256": hashlib.sha256(wire_b64.encode("ascii")).hexdigest(),
        "unsigned_path": str(Path(build_dir) / "unsigned.b64"),
        "blockhash": blockhash,
        "last_valid_block_height": last_valid,
        "block_height_at_gate": height,
        "blocks_left": blocks_left,
        "summary_age_s": round(issued - finished, 1),
        "issued_at": oplog.dt.datetime.fromtimestamp(issued, oplog.KYIV).isoformat(timespec="seconds"),
        "expires_at": oplog.dt.datetime.fromtimestamp(issued + TICKET_TTL_S, oplog.KYIV).isoformat(timespec="seconds"),
        "expires_at_epoch": issued + TICKET_TTL_S,
    }
    os.write(fd, json.dumps(ticket).encode())
    os.close(fd)
    tickets = Path(state_dir) / "tickets"
    tickets.mkdir(parents=True, exist_ok=True)
    (tickets / f"{ticket['ticket_id']}.json").write_text(json.dumps(ticket, indent=2), encoding="utf-8")
    return ticket


def run(build_dir: Path, *, log_path: Path | None = None, **kwargs) -> tuple[int, dict]:
    try:
        ticket = evaluate(build_dir, **kwargs)
    except Denied as exc:
        result = {"decision": "denied", "reason": exc.reason, **exc.facts}
        sha = exc.facts.get("message_sha256")
        oplog.append("gate", result, sha, path=log_path)
        return 2, result
    result = {"decision": "approved", "reason": "ok", "ticket": {k: v for k, v in ticket.items() if k != "unsigned_path"}}
    oplog.append("gate", result, ticket["message_sha256"], path=log_path)
    return 0, {**result, "ticket": ticket}


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] in ("-h", "--help"):
        print("usage: python3 tools/sign_gate.py BUILD_DIR   (exit 0 approved, 2 denied)", file=sys.stderr)
        return 1
    code, result = run(Path(argv[0]))
    print(json.dumps(result, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
