import base64
import unittest

from metis_offline.decode import evaluate
from metis_offline.fixtures import DEAD_BLOCKHASH, FEE_PAYER, LEGACY_B64, NONZERO_SIG_B64, PROGRAM_ID
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


if __name__ == "__main__":
    unittest.main()
