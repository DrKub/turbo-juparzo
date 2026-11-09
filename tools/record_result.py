"""Record a signature and its on-chain result in logs/operations.jsonl (read-only).

usage: python3 tools/record_result.py SIGNATURE [--ticket TICKET.json] [--signer PUBKEY]
                                      [--mechanism TEXT] [--backfilled]

Fetches getTransaction (base64, confirmed, maxSupportedTransactionVersion 0)
read-only. The transaction bytes are parsed in memory to recompute the message
sha256 and are never written anywhere. Appends a `sign` entry (signature,
signer, mechanism, ticket id) and a `result` entry: slot, blockTime, err, fee,
compute units, signer SOL delta, signer token deltas by mint, and whether the
on-chain message matches the ticket and landed within the ticket TTL.
"""

import base64
import datetime as dt
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from metis_offline.base58 import b58encode  # noqa: E402
from metis_offline.wire import parse_transaction  # noqa: E402
from tools import oplog  # noqa: E402
from tools.readonly_rpc import rpc as default_rpc  # noqa: E402

DEFAULT_SIGNER = "8Xm6iFvk9J8rBi7BvRAbiZU2RAEE1b7AM714QmiEXQ8a"


def fetch(signature: str, rpc=default_rpc, attempts: int = 10, delay: float = 2.0, sleep=time.sleep) -> dict | None:
    for _ in range(attempts):
        result = rpc("getTransaction", [signature, {"encoding": "base64", "commitment": "confirmed", "maxSupportedTransactionVersion": 0}])
        if result:
            return result
        sleep(delay)
    return None


def summarize(result: dict, signer: str, ticket: dict | None = None) -> tuple[dict, str]:
    raw = base64.b64decode(result["transaction"][0])
    tx = parse_transaction(raw)
    message_sha = hashlib.sha256(tx.message).hexdigest()
    meta = result.get("meta") or {}
    loaded = meta.get("loadedAddresses") or {}
    keys = [b58encode(k) for k in tx.account_keys] + list(loaded.get("writable") or []) + list(loaded.get("readonly") or [])
    sol_delta = None
    if signer in keys:
        i = keys.index(signer)
        sol_delta = meta["postBalances"][i] - meta["preBalances"][i]
    tokens: dict = {}
    for side, sign in (("preTokenBalances", -1), ("postTokenBalances", 1)):
        for bal in meta.get(side) or []:
            if bal.get("owner") != signer:
                continue
            amount = int((bal.get("uiTokenAmount") or {}).get("amount", "0"))
            tokens[bal["mint"]] = tokens.get(bal["mint"], 0) + sign * amount
    out = {
        "slot": result.get("slot"),
        "block_time": result.get("blockTime"),
        "block_time_kyiv": None if result.get("blockTime") is None else dt.datetime.fromtimestamp(result["blockTime"], oplog.KYIV).isoformat(timespec="seconds"),
        "err": meta.get("err"),
        "fee_lamports": meta.get("fee"),
        "compute_units": meta.get("computeUnitsConsumed"),
        "signer": signer,
        "signer_sol_delta_lamports": sol_delta,
        "signer_token_deltas_raw": tokens,
        "fee_payer": keys[0] if keys else None,
    }
    if ticket:
        out["ticket_id"] = ticket.get("ticket_id")
        out["message_matches_ticket"] = ticket.get("message_sha256") == message_sha
        expires = ticket.get("expires_at_epoch")
        out["landed_within_ticket_ttl"] = None if expires is None or result.get("blockTime") is None else result["blockTime"] <= expires + 1
    return out, message_sha


def record(signature: str, *, ticket: dict | None = None, signer: str = DEFAULT_SIGNER, mechanism: str = "Phantom embedded wallet via Phantom MCP, driven by the assistant outside the repo",
           backfilled: bool = False, rpc=default_rpc, log_path: Path | None = None, sleep=time.sleep) -> dict:
    sha_hint = ticket.get("message_sha256") if ticket else None
    oplog.append("sign", {"signature": signature, "signer": signer, "mechanism": mechanism, "ticket_id": ticket.get("ticket_id") if ticket else None},
                 sha_hint, backfilled, path=log_path)
    result = fetch(signature, rpc=rpc, sleep=sleep)
    if result is None:
        data = {"signature": signature, "status": "not_found"}
        oplog.append("result", data, sha_hint, backfilled, path=log_path)
        return data
    data, message_sha = summarize(result, signer, ticket)
    data = {"signature": signature, "status": "confirmed" if data["err"] is None else "failed", "fetched_at": oplog.now_iso(), **data}
    oplog.append("result", data, message_sha, backfilled, path=log_path)
    return data


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__, file=sys.stderr)
        return 1
    signature = argv[0]
    ticket = json.loads(Path(argv[argv.index("--ticket") + 1]).read_text()) if "--ticket" in argv else None
    signer = argv[argv.index("--signer") + 1] if "--signer" in argv else DEFAULT_SIGNER
    kwargs = {"mechanism": argv[argv.index("--mechanism") + 1]} if "--mechanism" in argv else {}
    data = record(signature, ticket=ticket, signer=signer, backfilled="--backfilled" in argv, **kwargs)
    print(json.dumps(data, indent=2))
    return 0 if data.get("status") == "confirmed" else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
