import unittest

from executor.book import fold
from executor.types import MarkRecord, Receipt, StoredQuote
from tests.fixtures import MINT_A


def quote(in_amount, out_amount):
    return StoredQuote("metis", in_amount, out_amount, None, None, None)


def buy(seq, out_amount="1000"):
    return Receipt(
        "buy1",
        "buy",
        "quoted",
        "signal",
        MINT_A,
        "80000000",
        out_amount,
        None,
        "not_configured",
        seq,
        80_000_000,
        5000,
        0,
        quote("80000000", out_amount),
    )


def sell(seq, signal_id="sell1"):
    return Receipt(
        signal_id,
        "sell",
        "quoted",
        "signal",
        MINT_A,
        "1000",
        "70000000",
        None,
        "not_configured",
        seq,
        None,
        None,
        None,
        quote("1000", "70000000"),
    )


class BookTest(unittest.TestCase):
    def test_fold_is_ordered_for_reopen_and_ignores_rejects(self):
        book = fold(1_000_000_000, [buy(1)], [])
        self.assertEqual(book.bank_lamports, 919_995_000)
        self.assertEqual(book.positions[MINT_A].token_base_units, 1000)
        self.assertFalse(book.positions[MINT_A].armed)
        closed = fold(1_000_000_000, [sell(2), buy(1)], [])
        self.assertEqual(closed.positions, {})
        self.assertEqual(closed.bank_lamports, 919_995_000 + 70_000_000)
        rejected = Receipt(
            "buy2",
            "buy",
            "rejected",
            "signal",
            MINT_A,
            None,
            None,
            "risk_cap",
            "not_configured",
            3,
            None,
            None,
            None,
            None,
        )
        self.assertEqual(fold(1_000_000_000, [rejected], []).bank_lamports, 1_000_000_000)
        mark = MarkRecord("buy1", 1, 120_000_000, 1000, 2, quote("1000", "120000000"))
        armed = fold(1_000_000_000, [buy(1, "1000")], [mark])
        self.assertTrue(armed.positions[MINT_A].armed)
        self.assertEqual(armed.positions[MINT_A].high_out_lamports, 120_000_000)


if __name__ == "__main__":
    unittest.main()
