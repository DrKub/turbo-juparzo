import unittest

from metis_offline.fixtures import DEAD_BLOCKHASH, FEE_PAYER, LEGACY_B64, PROGRAM_ID
from metis_offline.report import DecodeReport


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


if __name__ == "__main__":
    unittest.main()
