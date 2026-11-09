import unittest

from executor.records import mark_payload, receipt_payload
from executor.types import MarkRecord, Receipt, StoredQuote
from tests.fixtures import MINT_A


def quote() -> StoredQuote:
    return StoredQuote("metis", "80000000", "1000", 10, None, None)


class RecordsTest(unittest.TestCase):
    def test_receipt_status_is_dry_run_and_has_no_fill_fields(self):
        payload = receipt_payload(
            Receipt(
                "buy1",
                "buy",
                "quoted",
                "signal",
                MINT_A,
                "80000000",
                "1000",
                None,
                "not_configured",
                1,
                80_000_000,
                5000,
                0,
                quote(),
            )
        )
        self.assertEqual(payload["record_type"], "jupiter_dry_run_receipt")
        self.assertEqual(payload["status"], "dry_run")
        self.assertEqual(payload["seq"], "1")
        self.assertEqual(payload["quote"]["inAmount"], "80000000")
        forbidden = {"signature", "signed", "submitted", "filled", "transaction", "requestId"}
        self.assertTrue(forbidden.isdisjoint(payload))

    def test_rejected_receipt_nulls_quote_amounts(self):
        payload = receipt_payload(
            Receipt(
                "buy1",
                "buy",
                "rejected",
                "signal",
                MINT_A,
                None,
                None,
                "risk_cap",
                "configured",
                2,
                None,
                None,
                None,
                None,
            )
        )
        self.assertEqual(payload["reason"], "risk_cap")
        self.assertIsNone(payload["quoted_in_amount"])
        self.assertIsNone(payload["quote"])

    def test_mark_payload(self):
        payload = mark_payload(MarkRecord("buy1", 1, 112_000_000, 1_000_000_000, 3, quote()))
        self.assertEqual(payload["record_type"], "jupiter_dry_run_mark")
        self.assertEqual(payload["n"], "1")
        self.assertEqual(payload["mark_out_lamports"], "112000000")
        self.assertNotIn("status", payload)


if __name__ == "__main__":
    unittest.main()
