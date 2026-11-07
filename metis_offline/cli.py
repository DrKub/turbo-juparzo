import json
import sys
from pathlib import Path

from metis_offline.decode import evaluate
from metis_offline.report import DecodeReport, Intent

EXIT_HELP = 0
EXIT_FAILURE = 1
EXIT_REJECTED = 2


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv == ["--help"] or argv == ["-h"]:
        print("usage: metis-offline [FILE|-]", file=sys.stdout)
        print("Read one JSON request and print a rejected, unverified report.", file=sys.stdout)
        return EXIT_HELP
    if len(argv) > 1:
        print("usage: metis-offline [FILE|-]", file=sys.stderr)
        return EXIT_FAILURE
    try:
        text = _read_input(argv)
        intent, wire = _parse_request(text)
    except ValueError as exc:
        print(f"metis-offline: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    report = evaluate(wire, intent)
    json.dump(_public_report(report), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return EXIT_REJECTED


def _read_input(argv: list[str]) -> str:
    if not argv or argv[0] == "-":
        if not argv and sys.stdin.isatty():
            raise ValueError("usage: metis-offline [FILE|-]")
        return sys.stdin.read()
    path = Path(argv[0])
    if not path.is_file():
        raise ValueError(f"no such file: {path}")
    return path.read_text(encoding="utf-8")


def _parse_request(text: str) -> tuple[Intent, str]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("input is not valid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("input must be a JSON object")
    wire = data.get("transaction_b64")
    if not isinstance(wire, str) or wire == "":
        raise ValueError("missing transaction_b64")
    user = _hex_bytes(data.get("user_hex"), 32, "user_hex")
    input_mint = _hex_bytes(data.get("input_mint_hex"), 32, "input_mint_hex")
    output_mint = _hex_bytes(data.get("output_mint_hex"), 32, "output_mint_hex")
    amount = data.get("amount")
    if type(amount) is not int:
        raise ValueError("amount must be an integer")
    slippage = data.get("slippage_bps", None)
    if slippage is not None and type(slippage) is not int:
        raise ValueError("slippage_bps must be an integer or null")
    tables = data.get("captured_tables_hex", {})
    if not isinstance(tables, dict):
        raise ValueError("captured_tables_hex must be an object")
    captured = {}
    for key, value in tables.items():
        table_key = _hex_bytes(key, 32, "captured_tables_hex key")
        if not isinstance(value, str):
            raise ValueError("captured_tables_hex value must be hex")
        try:
            table_bytes = bytes.fromhex(value)
        except ValueError as exc:
            raise ValueError("captured_tables_hex value must be hex") from exc
        captured[table_key] = table_bytes
    return Intent(user, input_mint, output_mint, amount, slippage, captured), wire


def _hex_bytes(value: object, size: int, field: str) -> bytes:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be {size} bytes of hex")
    try:
        raw = bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be {size} bytes of hex") from exc
    if len(raw) != size:
        raise ValueError(f"{field} must be {size} bytes of hex")
    return raw


def _public_report(report: DecodeReport) -> dict:
    return {
        "fee_payer_hex": None if report.fee_payer is None else report.fee_payer.hex(),
        "instruction_semantics": report.instruction_semantics,
        "kind": report.kind,
        "parsed": report.parsed,
        "reason": report.reason,
        "signatures_all_zero": report.signatures_all_zero,
        "structural_decode": report.parsed,
        "trade_authorized": False,
        "version": report.version,
    }
