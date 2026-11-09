"""Append-only JSONL operation log, committed to git (logs/operations.jsonl).

Every entry: ts (Europe/Kyiv ISO), op, message_sha256 (or null), backfilled,
data. Before writing, the entry is scanned and refused (LogLeak) if it carries
anything that looks like raw transaction bytes or a secret:
- forbidden keys: transaction_b64, swapTransaction, data_b64, wire, raw, tx_bytes,
  and any key naming a secret (private, secret, mnemonic, seed, password,
  api_key, access_token, keypair);
- any string value longer than 100 characters made only of base64/base58
  characters (a Solana signature is 87-88 characters, a sha256 hex 64).
Public keys, signatures and hashes are allowed.
"""

import datetime as dt
import fcntl
import json
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = ROOT / "logs" / "operations.jsonl"
KYIV = ZoneInfo("Europe/Kyiv")
OPS = {"build", "check", "simulate", "gate", "sign", "result", "note"}
FORBIDDEN_KEYS = {"transaction_b64", "swaptransaction", "data_b64", "wire", "raw", "tx_bytes", "transaction"}
SECRET_KEY = re.compile(r"(private|secret|mnemonic|seed|password|api_?key|access_?token|keypair)", re.I)
BLOB = re.compile(r"^[A-Za-z0-9+/=_-]{101,}$")


class LogLeak(ValueError):
    pass


def now_iso() -> str:
    return dt.datetime.now(KYIV).isoformat(timespec="seconds")


def scan(value, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise LogLeak(f"non-string key at {path}")
            if key.lower() in FORBIDDEN_KEYS or SECRET_KEY.search(key):
                raise LogLeak(f"forbidden key {key!r} at {path}")
            scan(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            scan(item, f"{path}[{index}]")
    elif isinstance(value, (bytes, bytearray)):
        raise LogLeak(f"raw bytes at {path}")
    elif isinstance(value, str):
        if BLOB.match(value):
            raise LogLeak(f"blob-like string at {path}")


def make_entry(op: str, data: dict, message_sha256: str | None = None, backfilled: bool = False, ts: str | None = None) -> dict:
    if op not in OPS:
        raise ValueError(f"unknown op {op}")
    entry = {"ts": ts or now_iso(), "op": op, "message_sha256": message_sha256, "backfilled": backfilled, "data": data}
    scan(entry)
    return entry


def append(op: str, data: dict, message_sha256: str | None = None, backfilled: bool = False, ts: str | None = None, path: Path | None = None) -> dict:
    entry = make_entry(op, data, message_sha256, backfilled, ts)
    target = Path(path) if path else LOG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n"
    fd = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    return entry


def read(path: Path | None = None) -> list[dict]:
    target = Path(path) if path else LOG_PATH
    if not target.exists():
        return []
    return [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]


def log_build_summary(summary: dict, path: Path | None = None, backfilled: bool = False) -> str | None:
    """Turn a jupiter_unsigned_build summary into build / check / simulate entries."""
    report = (summary.get("metis_offline") or {}).get("report") or {}
    sha = report.get("message_sha256") or (summary.get("simulation") or {}).get("message_sha256")
    quote, build = summary.get("quote") or {}, summary.get("build") or {}
    append("build", {
        "quote_at": quote.get("at"), "in": quote.get("in"), "out": quote.get("out"), "min_out": quote.get("min_out"),
        "slippage_bps": quote.get("slippage_bps"), "price_impact_pct": quote.get("price_impact_pct"), "route": quote.get("route"),
        "built_at": build.get("at"), "blockhash": build.get("blockhash"), "last_valid_block_height": build.get("last_valid_block_height"),
        "jupiter_reported_priority_lamports": build.get("jupiter_reported_priority_lamports"),
        "jupiter_reported_cu_limit": build.get("jupiter_reported_cu_limit"), "wire_len": build.get("wire_len"),
    }, sha, backfilled, build.get("at"), path)
    metis = summary.get("metis_offline") or {}
    details = report.get("details") or {}
    append("check", {
        "tool": "metis-offline", "exit": metis.get("exit"), "kind": report.get("kind"), "reason": report.get("reason"),
        "instruction_semantics": report.get("instruction_semantics"), "trade_authorized": report.get("trade_authorized"),
        "fee_payer": report.get("fee_payer"), "fees": details.get("fees"), "accounts": details.get("accounts"),
        "swap": details.get("swap"), "freshness": details.get("freshness"), "alt": details.get("alt"),
    }, sha, backfilled, metis.get("at"), path)
    sim = dict(summary.get("simulation") or {})
    sim.pop("logs_tail", None)
    sim["freshness_end"] = summary.get("freshness_end")
    append("simulate", sim, sha, backfilled, sim.get("at"), path)
    return sha
