import base64
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from solders.hash import Hash
from solders.message import Message, MessageV0
from solders.null_signer import NullSigner
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from metis_offline.decode import check_amount, check_slippage, evaluate
from metis_offline.fixtures import DEAD_BLOCKHASH, FEE_PAYER, LEGACY_B64, NONZERO_SIG_B64, PROGRAM_ID, TABLE_ID, V0_B64
from metis_offline.report import DecodeReport, Intent


class ReportTest(unittest.TestCase):
    def test_report_cannot_say_approved(self):
        report = DecodeReport(False, None, "bad_base64", None, None)
        self.assertEqual(report.kind, "rejected")
        self.assertEqual(report.instruction_semantics, "unverified")
        self.assertNotEqual(report.kind, "approved")
        self.assertEqual(len(DEAD_BLOCKHASH), 32)
        self.assertEqual(DEAD_BLOCKHASH, bytes(32))
        self.assertEqual(len(FEE_PAYER), 32)
        self.assertEqual(len(PROGRAM_ID), 32)
        self.assertTrue(LEGACY_B64.startswith("AQAAAA"))


def intent():
    return Intent(FEE_PAYER, bytes([4]) * 32, bytes([5]) * 32, 1, None, {})


class DecodeTest(unittest.TestCase):
    def test_bad_base64_and_truncated_do_not_parse(self):
        for raw in ("????", base64.b64encode(b"\x01\x02").decode("ascii")):
            report = evaluate(raw, intent())
            self.assertFalse(report.parsed)
            self.assertIn(report.reason, {"bad_base64", "truncated"})
            self.assertEqual(report.kind, "rejected")

    def test_nonzero_signature_rejects(self):
        report = evaluate(NONZERO_SIG_B64, intent())
        self.assertEqual(report.reason, "signature_not_zero")
        self.assertEqual(report.signatures_all_zero, False)

    def test_legacy_parses_then_rejects_unknown_program(self):
        report = evaluate(LEGACY_B64, intent())
        self.assertTrue(report.parsed)
        self.assertEqual(report.version, "legacy")
        self.assertEqual(report.signatures_all_zero, True)
        self.assertEqual(report.fee_payer, FEE_PAYER)
        self.assertEqual(report.reason, "unknown_program")
        self.assertEqual(report.instruction_semantics, "unverified")

    def test_v0_fixture_has_zero_signature_blockhash_and_index_7(self):
        from metis_offline.synthetic import build_v0_b64

        self.assertEqual(V0_B64, build_v0_b64())
        tx = VersionedTransaction.from_bytes(base64.b64decode(V0_B64))
        self.assertIsInstance(tx.message, MessageV0)
        self.assertEqual(bytes(tx.signatures[0]), bytes(64))
        self.assertEqual(bytes(tx.message.recent_blockhash), bytes(32))
        self.assertEqual(bytes(tx.message.account_keys[0]), FEE_PAYER)
        self.assertEqual(bytes(tx.message.account_keys[1]), PROGRAM_ID)
        lookup = tx.message.address_table_lookups[0]
        self.assertEqual(bytes(lookup.account_key), TABLE_ID)
        self.assertEqual(list(lookup.writable_indexes), [7])
        self.assertEqual(list(lookup.readonly_indexes), [])

    def test_wrong_fee_payer_and_readonly_signer_reject(self):
        wrong = Intent(bytes([9]) * 32, bytes([4]) * 32, bytes([5]) * 32, 1, None, {})
        report = evaluate(LEGACY_B64, wrong)
        self.assertEqual(report.reason, "fee_payer")
        self.assertEqual(report.fee_payer, FEE_PAYER)
        mutable = bytearray(base64.b64decode(LEGACY_B64))
        # Message header starts after the one 64-byte zero signature.
        mutable[1 + 64 + 1] = 1
        readonly = base64.b64encode(bytes(mutable)).decode("ascii")
        flagged = evaluate(readonly, intent())
        self.assertEqual(flagged.reason, "readonly")

    def test_v0_without_captured_table_is_alt_missing(self):
        report = evaluate(V0_B64, intent())
        self.assertEqual(report.version, "v0")
        self.assertEqual(report.reason, "alt_missing")
        self.assertEqual(report.instruction_semantics, "unverified")
        self.assertEqual(report.kind, "rejected")

    def test_lookup_index_past_captured_table_is_alt_index(self):
        short = Intent(FEE_PAYER, bytes([4]) * 32, bytes([5]) * 32, 1, None, {TABLE_ID: bytes(32)})
        report = evaluate(V0_B64, short)
        self.assertEqual(report.reason, "alt_index")
        self.assertEqual(report.kind, "rejected")

    def test_non_aligned_table_bytes_are_alt_missing(self):
        odd = Intent(FEE_PAYER, bytes([4]) * 32, bytes([5]) * 32, 1, None, {TABLE_ID: bytes(31)})
        self.assertEqual(evaluate(V0_B64, odd).reason, "alt_missing")

    def test_resolved_lookup_is_still_unknown_program(self):
        covered = Intent(FEE_PAYER, bytes([4]) * 32, bytes([5]) * 32, 1, None, {TABLE_ID: bytes(32 * 8)})
        report = evaluate(V0_B64, covered)
        self.assertTrue(report.parsed)
        self.assertEqual(report.version, "v0")
        self.assertEqual(report.reason, "unknown_program")
        self.assertEqual(report.kind, "rejected")
        self.assertEqual(report.instruction_semantics, "unverified")

    def test_public_evaluator_rejects_every_slippage_value(self):
        payer = Pubkey.from_bytes(FEE_PAYER)
        message = Message.new_with_compiled_instructions(1, 0, 0, [payer], Hash.default(), [])
        wire = base64.b64encode(bytes(VersionedTransaction(message, [NullSigner(payer)]))).decode("ascii")
        for slippage in (None, 50):
            report = evaluate(wire, Intent(FEE_PAYER, bytes([4]) * 32, bytes([5]) * 32, 1, slippage, {}))
            self.assertTrue(report.parsed)
            self.assertEqual(report.version, "legacy")
            self.assertEqual(report.reason, "slippage_unapproved")
            self.assertEqual(report.kind, "rejected")
            self.assertEqual(report.instruction_semantics, "unverified")
        still_unknown = evaluate(LEGACY_B64, Intent(FEE_PAYER, bytes([4]) * 32, bytes([5]) * 32, 1, 50, {}))
        self.assertEqual(still_unknown.reason, "unknown_program")
        self.assertEqual(still_unknown.kind, "rejected")

    def test_v1_prefix_is_unsupported_version_and_short_input_stays_truncated(self):
        raw = bytearray(base64.b64decode(V0_B64))
        self.assertEqual(raw[65], 0x80)
        raw[65] = 0x81
        report = evaluate(base64.b64encode(bytes(raw)).decode("ascii"), intent())
        self.assertEqual(report.reason, "unsupported_version")
        self.assertEqual(report.version, "v1")
        self.assertFalse(report.parsed)
        self.assertEqual(report.kind, "rejected")
        self.assertEqual(report.instruction_semantics, "unverified")
        short = evaluate(base64.b64encode(b"\x01\x02").decode("ascii"), intent())
        self.assertEqual(short.reason, "truncated")
        self.assertIsNone(short.version)

    def test_required_signer_with_no_signature_vector_is_signature_count(self):
        parsed = VersionedTransaction.from_bytes(base64.b64decode(V0_B64))
        self.assertEqual(parsed.message.header.num_required_signatures, 1)
        bare = VersionedTransaction.populate(parsed.message, [])
        self.assertEqual(len(bare.signatures), 0)
        report = evaluate(base64.b64encode(bytes(bare)).decode("ascii"), intent())
        self.assertTrue(report.parsed)
        self.assertEqual(report.version, "v0")
        self.assertEqual(report.reason, "signature_count")
        self.assertEqual(report.kind, "rejected")
        self.assertEqual(report.instruction_semantics, "unverified")

    def test_example_slippage_is_not_approved(self):
        self.assertEqual(check_slippage(intent()), "slippage_unapproved")
        example = Intent(FEE_PAYER, bytes([4]) * 32, bytes([5]) * 32, 1, 50, {})
        self.assertEqual(check_slippage(example), "slippage_unapproved")

    def test_amount_mismatch_is_not_a_swap_approval(self):
        report = evaluate(LEGACY_B64, intent())
        self.assertEqual(report.reason, "unknown_program")
        self.assertNotEqual(report.kind, "approved")

    def test_matching_amount_is_still_not_approved(self):
        self.assertEqual(check_amount(intent(), 2), "intent_mismatch")
        self.assertEqual(check_amount(intent(), 1), "slippage_unapproved")

    def test_zero_signature_count_without_accounts_does_not_raise(self):
        raw = bytearray(base64.b64decode(LEGACY_B64))
        raw[0] = 0
        report = evaluate(base64.b64encode(bytes(raw)).decode("ascii"), intent())
        self.assertEqual(report.kind, "rejected")
        self.assertEqual(report.reason, "truncated")
        self.assertEqual(report.instruction_semantics, "unverified")

    def test_package_has_no_network_client(self):
        from pathlib import Path

        text = "\n".join(path.read_text(encoding="utf-8") for path in Path("metis_offline").rglob("*.py"))
        self.assertNotIn("urlopen", text)
        self.assertNotIn("/execute", text)
        self.assertNotIn("/build", text)
        self.assertNotIn("solana_send", text)


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "metis_offline" / "samples" / "legacy-unknown.json"


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
    def test_sample_file_is_rejected_after_structural_decode(self):
        code, out, err = run_cli([str(SAMPLE)])
        self.assertEqual(err, "")
        self.assertEqual(code, 2)
        body = json.loads(out)
        self.assertEqual(body["kind"], "rejected")
        self.assertEqual(body["instruction_semantics"], "unverified")
        self.assertIs(body["trade_authorized"], False)
        self.assertIs(body["structural_decode"], True)
        self.assertEqual(body["reason"], "unknown_program")
        self.assertEqual(body["version"], "legacy")
        self.assertEqual(body["fee_payer_hex"], FEE_PAYER.hex())

    def test_stdin_bad_base64_is_a_rejection_not_a_crash(self):
        payload = {
            "transaction_b64": "????",
            "user_hex": FEE_PAYER.hex(),
            "input_mint_hex": (bytes([4]) * 32).hex(),
            "output_mint_hex": (bytes([5]) * 32).hex(),
            "amount": 1,
            "slippage_bps": None,
        }
        code, out, err = run_cli(["-"], json.dumps(payload))
        self.assertEqual(code, 2)
        self.assertEqual(err, "")
        body = json.loads(out)
        self.assertEqual(body["reason"], "bad_base64")
        self.assertIs(body["structural_decode"], False)
        self.assertIs(body["trade_authorized"], False)

    def test_invalid_json_and_missing_file_are_program_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text("{", encoding="utf-8")
            code, out, err = run_cli([str(bad)])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("JSON", err)
        code, out, err = run_cli([str(ROOT / "missing-metis-input.json")])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("no such file", err)

    def test_wrapper_selects_the_repo_venv_from_another_directory(self):
        proc = subprocess.run(
            [str(ROOT / "metis-offline"), str(SAMPLE)],
            cwd=tempfile.gettempdir(),
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, "")
        body = json.loads(proc.stdout)
        self.assertEqual(body["reason"], "unknown_program")
        self.assertIs(body["trade_authorized"], False)


if __name__ == "__main__":
    unittest.main()
