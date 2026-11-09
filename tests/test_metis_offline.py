"""Offline preflight tests. Standard library only: no solders, no network."""

import base64
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path

from metis_offline import fixtures as fx
from metis_offline.alt import ALT_PROGRAM, MAX_U64, parse_table, usable_count
from metis_offline.base58 import b58decode, b58encode, pubkey
from metis_offline.pda import associated_token_address, is_on_curve
from metis_offline.preflight import (
    ATA,
    MAX_CU,
    SYSTEM,
    TOKEN,
    USDC,
    WSOL,
    Freshness,
    TableEvidence,
    default_cu_limit,
    preflight,
    priority_fee_lamports,
)
from metis_offline.wire import CompiledInstruction, Lookup, WireError, parse_transaction, serialize

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "metis_offline" / "samples" / "synthetic-direct-route.json"


def run(case: fx.Case):
    return preflight(case.raw(), case.intent, case.evidence())


def with_ix(case: fx.Case, index: int, ix) -> fx.Case:
    case.instructions[index] = ix
    return case


def index_of(case: fx.Case, program_index: int, first_byte: int | None = None) -> int:
    for i, ix in enumerate(case.instructions):
        if ix.program_index == program_index and (first_byte is None or ix.data[:1] == bytes([first_byte])):
            return i
    raise AssertionError("instruction not found")


class Base58AndPdaTest(unittest.TestCase):
    def test_round_trip_and_leading_zeros(self):
        for raw in (bytes(32), bytes([0, 0, 1]) + bytes(29), bytes(range(32))):
            self.assertEqual(b58decode(b58encode(raw)), raw)
        self.assertEqual(b58encode(bytes(32)), "11111111111111111111111111111111")
        with self.assertRaises(ValueError):
            pubkey("0OIl")
        with self.assertRaises(ValueError):
            pubkey("1111")

    def test_known_mainnet_associated_token_address(self):
        # Public on-chain fact: the USDC ATA of 8Xm6... is 4E1xT2... (seen in RPC data).
        owner = pubkey("8Xm6iFvk9J8rBi7BvRAbiZU2RAEE1b7AM714QmiEXQ8a")
        ata = associated_token_address(owner, USDC, TOKEN, ATA)
        self.assertEqual(b58encode(ata), "4E1xT2iA5EkrVGzrNyLcFfy9G9GJtamg63bSbodQDxce")
        self.assertFalse(is_on_curve(ata))
        self.assertTrue(is_on_curve(owner))


class WireTest(unittest.TestCase):
    def test_round_trip_v0(self):
        raw = fx.Case().raw()
        tx = parse_transaction(raw)
        self.assertEqual(tx.version, "v0")
        self.assertEqual(serialize(tx), raw)
        self.assertEqual(len(tx.lookups), 1)

    def test_truncated_trailing_and_unsupported_version(self):
        raw = fx.Case().raw()
        with self.assertRaises(WireError) as ctx:
            parse_transaction(raw[:-3])
        self.assertEqual(ctx.exception.reason, "truncated")
        with self.assertRaises(WireError) as ctx:
            parse_transaction(raw + b"\0")
        self.assertEqual(ctx.exception.reason, "trailing_bytes")
        bumped = bytearray(raw)
        bumped[65] = 0x81
        with self.assertRaises(WireError) as ctx:
            parse_transaction(bytes(bumped))
        self.assertEqual((ctx.exception.reason, ctx.exception.version), ("unsupported_version", "v1"))


class AltTest(unittest.TestCase):
    def test_header_layout_and_states(self):
        reason, table = parse_table(fx.table_data(), ALT_PROGRAM)
        self.assertIsNone(reason)
        self.assertEqual(table.deactivation_slot, MAX_U64)
        self.assertEqual(len(table.addresses), 4)
        self.assertEqual(parse_table(fx.table_data(), SYSTEM)[0], "alt_owner")
        self.assertEqual(parse_table(fx.table_data()[:-1], ALT_PROGRAM)[0], "alt_layout")
        self.assertEqual(parse_table(fx.table_data(kind=0), ALT_PROGRAM)[0], "alt_layout")
        self.assertEqual(parse_table(fx.table_data(deactivation=5), ALT_PROGRAM)[0], "alt_deactivated")

    def test_warmup_limits_usable_addresses(self):
        _, table = parse_table(fx.table_data(last_extended=200, start=2), ALT_PROGRAM)
        self.assertEqual(usable_count(table, 200), (None, 2))
        self.assertEqual(usable_count(table, 201), (None, 4))
        self.assertEqual(usable_count(table, 199)[0], "alt_context")
        self.assertEqual(usable_count(table, None)[0], "alt_context")


class PositiveTest(unittest.TestCase):
    def test_synthetic_direct_route_passes_semantics(self):
        report = run(fx.Case())
        self.assertEqual(report.reason, "ok_unsigned")
        self.assertTrue(report.semantic_pass)
        self.assertEqual(report.kind, "semantic_pass")
        swap = report.details["swap"]
        self.assertEqual((swap["in_amount"], swap["quoted_out_amount"], swap["slippage_bps"]), (1_000_000, 86_000, 50))
        self.assertEqual(swap["min_out_amount_floor"], 86_000 * 9_950 // 10_000)
        self.assertEqual(swap["route_steps"], 1)
        self.assertEqual(report.details["accounts"]["intermediate_accounts"], 0)
        self.assertEqual(report.details["fees"]["compute_unit_limit_source"], "explicit")
        self.assertEqual(report.details["fees"]["priority_fee_lamports"], 90)
        self.assertEqual(report.details["fees"]["network_fee_lamports"], 5_090)

    def test_destination_usdc_ata_create_is_allowed(self):
        case = fx.Case()
        case.instructions.insert(3, fx.ata_create_ix(fx.I_USDC_ATA, fx.I_USDC))
        report = run(case)
        self.assertEqual(report.reason, "ok_unsigned")
        self.assertTrue(report.details["accounts"]["usdc_destination_ata_create_idempotent"])


class FeeTest(unittest.TestCase):
    def test_priority_formula_rounds_up(self):
        self.assertEqual(priority_fee_lamports(116_718, 85_676), 10_000)
        self.assertEqual(priority_fee_lamports(10, 1_206_000), 13)
        self.assertEqual(priority_fee_lamports(1, 1), 1)
        self.assertEqual(priority_fee_lamports(0, 1_400_000), 0)

    def test_explicit_cu_limit_overrides_default(self):
        # Price 100_000 with explicit limit 90_000 -> 9_000 lamports: passes.
        case = fx.Case(instructions=fx.default_instructions(cu_limit=90_000, cu_price=100_000))
        report = run(case)
        self.assertEqual(report.reason, "ok_unsigned")
        self.assertEqual(report.details["fees"]["compute_unit_limit"], 90_000)
        self.assertEqual(report.details["fees"]["priority_fee_lamports"], 9_000)
        # Same price without the explicit limit uses the default sum and exceeds the cap.
        case = fx.Case(instructions=fx.default_instructions(cu_limit=None, cu_price=100_000))
        self.assertEqual(run(case).reason, "priority_fee_over_limit")

    def test_default_cu_limit_counts_builtins(self):
        case = fx.Case(instructions=fx.default_instructions(cu_limit=None, cu_price=1))
        report = run(case)
        self.assertEqual(report.reason, "ok_unsigned")
        # ComputeBudget + System are builtins (3k); ATA, Token x2, Jupiter are not (200k).
        self.assertEqual(report.details["fees"]["compute_unit_limit"], 2 * 3_000 + 4 * 200_000)
        self.assertEqual(report.details["fees"]["compute_unit_limit_source"], "default")
        self.assertEqual(default_cu_limit([TOKEN] * 10), MAX_CU)

    def test_explicit_limit_above_cap_is_clamped_and_large_override_is_rejected(self):
        case = fx.Case(instructions=fx.default_instructions(cu_limit=5_000_000, cu_price=1_000))
        report = run(case)
        self.assertEqual(report.details["fees"]["compute_unit_limit"], MAX_CU)
        case = fx.Case(instructions=fx.default_instructions(cu_limit=1_400_000, cu_price=10_000))
        self.assertEqual(run(case).reason, "priority_fee_over_limit")

    def test_request_cap_can_only_tighten(self):
        case = fx.Case()
        case.intent = replace(case.intent, max_priority_fee_lamports=50)
        self.assertEqual(run(case).reason, "priority_fee_over_limit")
        case.intent = replace(case.intent, max_priority_fee_lamports=20_000)
        self.assertEqual(run(case).reason, "priority_fee_over_limit")

    def test_absent_price_is_zero_priority_and_duplicate_is_rejected(self):
        case = fx.Case()
        del case.instructions[1]
        report = run(case)
        self.assertEqual(report.reason, "ok_unsigned")
        self.assertEqual(report.details["fees"]["priority_fee_lamports"], 0)
        self.assertEqual(report.details["fees"]["compute_unit_price_source"], "absent")
        case = fx.Case()
        case.instructions.insert(0, fx.cu_price_ix(1))
        self.assertEqual(run(case).reason, "duplicate_compute_budget")


class AltRejectTest(unittest.TestCase):
    def table_case(self, **kwargs) -> fx.Case:
        case = fx.Case()
        case.tables = [replace(case.tables[0], **kwargs)]
        return case

    def test_missing_table(self):
        case = fx.Case()
        case.tables = []
        self.assertEqual(run(case).reason, "alt_missing")

    def test_wrong_owner(self):
        self.assertEqual(run(self.table_case(owner=SYSTEM)).reason, "alt_owner")

    def test_bad_layout(self):
        self.assertEqual(run(self.table_case(data=fx.table_data()[:60])).reason, "alt_layout")
        self.assertEqual(run(self.table_case(data=fx.table_data(kind=2))).reason, "alt_layout")

    def test_deactivated(self):
        self.assertEqual(run(self.table_case(data=fx.table_data(deactivation=123))).reason, "alt_deactivated")

    def test_index_out_of_bounds(self):
        self.assertEqual(run(self.table_case(data=fx.table_data(addresses=fx.TABLE_ADDRESSES[:3]))).reason, "alt_index")
        case = fx.Case()
        case.lookups = [Lookup(fx.TABLE, (0,), (1, 2, 9))]
        self.assertEqual(run(case).reason, "alt_index")

    def test_warming_or_future_table(self):
        case = self.table_case(data=fx.table_data(last_extended=200, start=2))
        self.assertEqual(run(case).reason, "alt_index")
        self.assertEqual(run(self.table_case(context_slot=50)).reason, "alt_context")
        self.assertEqual(run(self.table_case(context_slot=None)).reason, "alt_context")

    def test_table_content_swap_changes_semantics(self):
        # Same table key, but the loaded mint is not USDC: semantics must fail.
        other = bytes([7]) * 32
        data = fx.table_data(addresses=[fx.POOL, WSOL, other, fx.AMM_PROGRAM])
        self.assertEqual(run(self.table_case(data=data)).reason, "mint_mismatch")


class SwapRejectTest(unittest.TestCase):
    def route_case(self, **kwargs) -> fx.Case:
        case = fx.Case()
        i = index_of(case, fx.I_JUP)
        return with_ix(case, i, fx.route_ix(fx.route_data(**kwargs)))

    def test_amount_over_limit_and_mismatch(self):
        case = fx.Case()
        case.intent = replace(case.intent, amount=1_000_001)
        self.assertEqual(run(case).reason, "amount_over_limit")
        self.assertEqual(run(self.route_case(in_amount=999_999)).reason, "amount_mismatch")
        case = fx.Case()
        with_ix(case, index_of(case, fx.I_SYSTEM), fx.transfer_ix(2_000_000))
        self.assertEqual(run(case).reason, "amount_mismatch")

    def test_slippage_over_limit_and_mismatch(self):
        case = fx.Case()
        case.intent = replace(case.intent, slippage_bps=51)
        self.assertEqual(run(case).reason, "slippage_over_limit")
        self.assertEqual(run(self.route_case(slippage=51)).reason, "slippage_over_limit")
        self.assertEqual(run(self.route_case(slippage=30)).reason, "slippage_mismatch")

    def test_platform_or_positive_slippage_fee(self):
        self.assertEqual(run(self.route_case(platform_fee=10)).reason, "platform_fee")
        self.assertEqual(run(self.route_case(positive=5)).reason, "platform_fee")

    def test_multi_step_or_split_route_is_unsupported(self):
        two = [bytes.fromhex("b101ec0e0001"), bytes.fromhex("7401102701 03".replace(" ", ""))]
        self.assertEqual(run(self.route_case(steps=two)).reason, "unsupported_route")
        split = [bytes.fromhex("2f01001027" "00 01".replace(" ", ""))[:-4] + (6180).to_bytes(2, "little") + b"\x00\x01"]
        self.assertEqual(run(self.route_case(steps=split)).reason, "unsupported_route")

    def test_other_jupiter_instruction_is_unsupported(self):
        case = fx.Case()
        data = bytes.fromhex("e517cb977ae3ad2a") + fx.route_data()[8:]
        with_ix(case, index_of(case, fx.I_JUP), fx.route_ix(data))
        self.assertEqual(run(case).reason, "unsupported_route")

    def test_wrong_destination_or_authority(self):
        case = fx.Case()
        accounts = list(fx.route_ix().accounts)
        accounts[2] = fx.I_POOL
        with_ix(case, index_of(case, fx.I_JUP), fx.route_ix(accounts=tuple(accounts)))
        self.assertEqual(run(case).reason, "route_accounts")

    def test_user_authority_in_hop_accounts_is_allowed(self):
        case = fx.Case()
        accounts = fx.route_ix().accounts + (fx.I_USER,)
        with_ix(case, index_of(case, fx.I_JUP), fx.route_ix(accounts=accounts))
        self.assertEqual(run(case).reason, "ok_unsigned")

    def test_unknown_amm_program_is_rejected(self):
        case = fx.Case()
        case.tables = [replace(case.tables[0], data=fx.table_data(addresses=[fx.POOL, WSOL, USDC, bytes([3]) * 32]))]
        self.assertEqual(run(case).reason, "unsupported_amm")

    def test_wrong_pair(self):
        case = fx.Case()
        case.intent = replace(case.intent, output_mint=bytes([8]) * 32)
        self.assertEqual(run(case).reason, "unsupported_pair")

    def test_fee_payer_mismatch(self):
        case = fx.Case()
        case.intent = replace(case.intent, user=bytes([6]) * 32)
        self.assertEqual(run(case).reason, "fee_payer")


class InstructionRejectTest(unittest.TestCase):
    def test_approve_is_forbidden(self):
        for tag in (4, 13):
            case = fx.Case()
            case.instructions.insert(5, CompiledInstruction(fx.I_TOKEN, (fx.I_WSOL_ATA, fx.I_POOL, fx.I_USER), bytes([tag]) + bytes(8)))
            self.assertEqual(run(case).reason, "approve_forbidden")

    def test_intermediate_ata_not_closed_fails_closed(self):
        # A third mint (e.g. USDT) created by the route: unclosable intermediate account.
        usdt = pubkey("Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB")
        usdt_ata = associated_token_address(fx.USER, usdt, TOKEN, ATA)
        # Insert usdt_ata as writable static key 3 and usdt mint as readonly key 10;
        # every later index shifts accordingly.
        case = fx.Case()
        case.static = fx.STATIC[:3] + [usdt_ata] + fx.STATIC[3:] + [usdt]
        shift = lambda i: i + 1 if i >= 3 else i  # noqa: E731
        moved = []
        for ix in fx.default_instructions():
            idx = tuple(shift(a) if a < 9 else a + 2 for a in ix.accounts)
            moved.append(CompiledInstruction(shift(ix.program_index), idx, ix.data))
        moved.insert(3, CompiledInstruction(shift(fx.I_ATA), (0, 3, 0, 10, shift(fx.I_SYSTEM), shift(fx.I_TOKEN)), b"\x01"))
        case.instructions = moved
        report = run(case)
        self.assertEqual(report.reason, "unsupported_intermediate_account")

    def test_wsol_ata_not_closed_fails_closed(self):
        case = fx.Case()
        del case.instructions[index_of(case, fx.I_TOKEN, 9)]
        self.assertEqual(run(case).reason, "missing_close")

    def test_close_to_someone_else_is_rejected(self):
        case = fx.Case()
        with_ix(case, index_of(case, fx.I_TOKEN, 9), fx.close_ix(dest=fx.I_POOL))
        self.assertEqual(run(case).reason, "unsupported_instruction")

    def test_system_create_account_is_intermediate(self):
        case = fx.Case()
        case.instructions.insert(2, CompiledInstruction(fx.I_SYSTEM, (fx.I_USER, fx.I_POOL), (0).to_bytes(4, "little") + bytes(48)))
        self.assertEqual(run(case).reason, "unsupported_intermediate_account")

    def test_extra_transfer_tip_is_rejected(self):
        case = fx.Case()
        case.instructions.insert(6, fx.transfer_ix(1_000, dest=fx.I_POOL))
        self.assertEqual(run(case).reason, "unexpected_transfer")

    def test_unknown_program_is_rejected(self):
        case = fx.Case()
        case.instructions.insert(0, CompiledInstruction(fx.I_EVENT, (), b"x"))
        self.assertEqual(run(case).reason, "unknown_program")

    def test_order_is_enforced(self):
        case = fx.Case()
        i, j = index_of(case, fx.I_TOKEN, 17), index_of(case, fx.I_JUP)
        case.instructions[i], case.instructions[j] = case.instructions[j], case.instructions[i]
        self.assertEqual(run(case).reason, "instruction_order")


class EnvelopeRejectTest(unittest.TestCase):
    def test_stale_blockhash(self):
        case = fx.Case()
        case.freshness = Freshness(fx.BLOCKHASH, 1_000, 1_001)
        report = run(case)
        self.assertEqual(report.reason, "stale_blockhash")
        self.assertFalse(report.semantic_pass)
        case.freshness = Freshness(fx.BLOCKHASH, 1_000, 1_000)
        self.assertEqual(run(case).reason, "ok_unsigned")

    def test_freshness_missing_or_for_another_blockhash(self):
        case = fx.Case()
        case.freshness = None
        self.assertEqual(run(case).reason, "freshness_missing")
        case.freshness = Freshness(bytes([8]) * 32, 1_000, 900)
        self.assertEqual(run(case).reason, "blockhash_mismatch")

    def test_signature_present_is_not_an_unsigned_build(self):
        case = fx.Case()
        case.signatures = [bytes([1]) * 64]
        report = run(case)
        self.assertEqual(report.reason, "signature_present")
        self.assertFalse(report.signatures_all_zero)

    def test_signature_count_and_signer_layout(self):
        case = fx.Case()
        case.signatures = []
        self.assertEqual(run(case).reason, "signature_count")
        case = fx.Case()
        case.header = (1, 1, 5)
        self.assertEqual(run(case).reason, "signer_layout")

    def test_legacy_message_is_unsupported(self):
        case = fx.Case()
        case.version = "legacy"
        report = run(case)
        self.assertTrue(report.parsed)
        self.assertEqual(report.reason, "unsupported_version")


def run_cli(argv, stdin_text=None):
    from metis_offline.cli import main

    old = sys.stdin
    if stdin_text is not None:
        sys.stdin = StringIO(stdin_text)
    out, err = StringIO(), StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
    finally:
        sys.stdin = old
    return code, out.getvalue(), err.getvalue()


class CliTest(unittest.TestCase):
    def test_committed_sample_matches_builder_and_passes(self):
        self.assertEqual(json.loads(SAMPLE.read_text(encoding="utf-8")), fx.Case().request())
        code, out, err = run_cli([str(SAMPLE)])
        self.assertEqual((code, err), (0, ""))
        body = json.loads(out)
        self.assertEqual(body["kind"], "semantic_pass")
        self.assertEqual(body["instruction_semantics"], "verified_route_v2_direct")
        self.assertIs(body["trade_authorized"], False)
        self.assertIs(body["signatures_authentic"], False)
        self.assertNotIn(fx.Case().request()["transaction_b64"], out)

    def test_cli_rejects_with_exit_2(self):
        case = fx.Case()
        case.tables = []
        code, out, err = run_cli(["-"], json.dumps(case.request()))
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["reason"], "alt_missing")
        self.assertIs(json.loads(out)["trade_authorized"], False)

    def test_raw_base64_is_structural_only(self):
        wire = base64.b64encode(fx.Case().raw()).decode("ascii")
        code, out, err = run_cli(["-"], wire + "\n")
        self.assertEqual(code, 2)
        body = json.loads(out)
        self.assertEqual(body["reason"], "intent_missing")
        self.assertIs(body["structural_decode"], True)
        self.assertNotIn(wire, out)

    def test_bad_inputs(self):
        code, out, err = run_cli(["-"], "")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("empty input", err)
        code, out, _ = run_cli(["-"], "????")
        self.assertEqual((code, json.loads(out)["reason"]), (2, "bad_base64"))
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text("{", encoding="utf-8")
            code, out, err = run_cli([str(bad)])
        self.assertEqual((code, out), (1, ""))
        self.assertIn("JSON", err)
        code, out, err = run_cli([str(ROOT / "missing-metis-input.json")])
        self.assertEqual(code, 1)
        self.assertIn("no such file", err)
        request = fx.Case().request()
        request["intent"]["amount"] = "1000000"
        code, out, err = run_cli(["-"], json.dumps(request))
        self.assertEqual((code, out), (1, ""))
        huge = "A" * 4_194_305
        code, out, err = run_cli(["-"], huge)
        self.assertEqual((code, out), (1, ""))
        self.assertLess(len(err), 500)

    def test_launcher_uses_system_python_from_another_directory(self):
        proc = subprocess.run([str(ROOT / "metis-offline"), str(SAMPLE)], cwd=tempfile.gettempdir(), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["reason"], "ok_unsigned")


class HygieneTest(unittest.TestCase):
    def test_package_is_offline_stdlib_and_never_signs(self):
        text = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "metis_offline").rglob("*.py"))
        for needle in ("urlopen", "solders", "socket", "solana_send", "Keypair(", "sign_message", "send_transaction", "sendTransaction", "/execute"):
            self.assertNotIn(needle, text)

    def test_build_helper_is_read_only(self):
        text = (ROOT / "tools" / "jupiter_unsigned_build.py").read_text(encoding="utf-8")
        for needle in ("sendTransaction", "sendRawTransaction", "requestAirdrop", "solana_send", "Keypair", "/execute", "private"):
            self.assertNotIn(needle, text)
        self.assertIn('"replaceRecentBlockhash": False', text)
        self.assertIn('"sigVerify": False', text)
        self.assertIn('"onlyDirectRoutes": "true"', text)
        import importlib.util

        spec = importlib.util.spec_from_file_location("jub", ROOT / "tools" / "jupiter_unsigned_build.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertNotIn("sendTransaction", module.READ_ONLY_METHODS)
        with self.assertRaises(RuntimeError):
            module.rpc("sendTransaction", [])

    def test_repo_has_no_solders_dependency(self):
        import re

        pattern = re.compile(r"^\s*(from|import)\s+" + "sol" + "ders", re.M)
        for path in list((ROOT / "tests").rglob("*.py")) + list((ROOT / "executor").rglob("*.py")) + list((ROOT / "metis_offline").rglob("*.py")):
            self.assertIsNone(pattern.search(path.read_text(encoding="utf-8")), str(path))
        self.assertFalse((ROOT / "requirements-metis-offline.txt").exists())


if __name__ == "__main__":
    unittest.main()
