import base64
import binascii
import json
import sys
from pathlib import Path

from metis_offline.base58 import b58encode, pubkey
from metis_offline.preflight import Evidence, Freshness, Intent, Report, TableEvidence, preflight
from metis_offline.wire import WireError, parse_transaction

EXIT_PASS = 0
EXIT_FAILURE = 1
EXIT_REJECTED = 2
MAX_INPUT_CHARS = 4_194_304
USAGE = "usage: metis-offline [FILE|-]"


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv == ["--help"] or argv == ["-h"]:
        print(USAGE, file=sys.stdout)
        print("Read a JSON preflight request (or bare base64) and print a JSON report.", file=sys.stdout)
        print("Exit 0: semantic pass of an unsigned build (never a trade authorization). 2: rejected. 1: bad request.", file=sys.stdout)
        return EXIT_PASS
    if len(argv) > 1:
        print(USAGE, file=sys.stderr)
        return EXIT_FAILURE
    try:
        text = _read_input(argv)
        request = _parse_request(text)
    except ValueError as exc:
        print(f"metis-offline: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    wire_b64, intent, evidence = request
    try:
        raw = base64.b64decode(wire_b64, validate=True)
    except (binascii.Error, ValueError):
        report = Report("bad_base64")
    else:
        if intent is None:
            report = _structural_only(raw)
        else:
            report = preflight(raw, intent, evidence)
    json.dump(public_report(report), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return EXIT_PASS if report.semantic_pass else EXIT_REJECTED


def _structural_only(raw: bytes) -> Report:
    report = Report("intent_missing")
    try:
        tx = parse_transaction(raw)
    except WireError as exc:
        report.reason = exc.reason
        report.version = exc.version
        return report
    report.parsed = True
    report.version = tx.version
    if len(tx.signatures) != tx.num_required_signatures:
        report.reason = "signature_count"
        return report
    report.signatures_all_zero = all(sig == bytes(64) for sig in tx.signatures)
    if tx.account_keys:
        report.fee_payer = tx.account_keys[0]
    return report


def _read_input(argv: list[str]) -> str:
    if not argv or argv[0] == "-":
        if not argv and sys.stdin.isatty():
            raise ValueError(USAGE)
        return sys.stdin.read()
    path = Path(argv[0])
    if not path.is_file():
        raise ValueError(f"no such file: {path}")
    return path.read_text(encoding="utf-8")


def _parse_request(text: str):
    if len(text) > MAX_INPUT_CHARS:
        raise ValueError(f"input exceeds {MAX_INPUT_CHARS} characters")
    stripped = text.strip()
    if stripped == "":
        raise ValueError("empty input")
    if not stripped.startswith("{"):
        return "".join(stripped.split()), None, Evidence()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("input is not valid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("input must be a JSON object")
    wire = data.get("transaction_b64")
    if not isinstance(wire, str) or wire == "":
        raise ValueError("missing transaction_b64")
    raw_intent = data.get("intent")
    if not isinstance(raw_intent, dict):
        raise ValueError("missing intent object")
    intent = Intent(
        user=_key(raw_intent, "user"),
        input_mint=_key(raw_intent, "input_mint"),
        output_mint=_key(raw_intent, "output_mint"),
        amount=_int(raw_intent, "amount"),
        slippage_bps=_int(raw_intent, "slippage_bps"),
        max_priority_fee_lamports=_int(raw_intent, "max_priority_fee_lamports", 10_000),
    )
    tables = []
    raw_tables = data.get("lookup_tables", [])
    if not isinstance(raw_tables, list):
        raise ValueError("lookup_tables must be a list")
    for item in raw_tables:
        if not isinstance(item, dict):
            raise ValueError("lookup_tables entries must be objects")
        blob = item.get("data_b64")
        if not isinstance(blob, str):
            raise ValueError("lookup_tables.data_b64 must be base64")
        try:
            table_bytes = base64.b64decode(blob, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("lookup_tables.data_b64 must be base64") from exc
        slot = item.get("context_slot")
        if slot is not None and type(slot) is not int:
            raise ValueError("lookup_tables.context_slot must be an integer")
        tables.append(TableEvidence(_key(item, "address"), _key(item, "owner"), table_bytes, slot))
    freshness = None
    raw_fresh = data.get("freshness")
    if raw_fresh is not None:
        if not isinstance(raw_fresh, dict):
            raise ValueError("freshness must be an object")
        freshness = Freshness(
            _key(raw_fresh, "blockhash"),
            _int(raw_fresh, "last_valid_block_height"),
            _int(raw_fresh, "current_block_height"),
        )
    return wire, intent, Evidence(tuple(tables), freshness)


def _key(obj: dict, name: str) -> bytes:
    value = obj.get(name)
    try:
        return pubkey(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name} must be a base58 32-byte address") from exc


def _int(obj: dict, name: str, default: int | None = None) -> int:
    value = obj.get(name, default)
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer")
    return value


def public_report(report: Report) -> dict:
    passed = report.semantic_pass
    return {
        "kind": report.kind,
        "reason": report.reason,
        "structural_decode": report.parsed,
        "version": report.version,
        "fee_payer": None if report.fee_payer is None else b58encode(report.fee_payer),
        "message_sha256": report.message_sha256,
        "signatures_all_zero": report.signatures_all_zero,
        "signatures_authentic": False,
        "instruction_semantics": "verified_route_v2_direct" if passed else "unverified",
        "economics_verified": passed,
        "trade_authorized": False,
        "details": report.details,
    }
