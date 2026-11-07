import base64
import unittest

from solders.message import MessageV0
from solders.transaction import VersionedTransaction

from metis_offline.decode import evaluate
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


if __name__ == "__main__":
    unittest.main()
