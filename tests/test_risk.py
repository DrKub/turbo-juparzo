import unittest

from executor.risk import denylist_label, prequote
from executor.types import SOL_MINT, Book, BuyEstimate, DryRunConfig, Position, Signal
from tests.fixtures import MINT_A, MINT_B, MINT_C, MINT_D, MINT_DENIED


def config(bank=1_000_000_000, denylist=None, estimates=None, mints=None):
    return DryRunConfig(
        bank,
        mints if mints is not None else {MINT_A: 6, MINT_B: 6, MINT_C: 6, MINT_D: 6},
        denylist,
        estimates if estimates is not None else {"buy1": BuyEstimate(5000, 0)},
    )


def buy(mint=MINT_A, lamports=80_000_000, signal_id="buy1"):
    return Signal(signal_id, "buy", mint, "t", (0, signal_id, 0), lamports, None)


def sell(amount=1000, mint=MINT_A, signal_id="sell1"):
    return Signal(signal_id, "sell", mint, "t", (1, signal_id, 1), None, amount)


def position(mint=MINT_A):
    return Position("buy1", mint, 80_000_000, 1000, 1000, 80_000_000, 1000, False)


class RiskTest(unittest.TestCase):
    def test_cap_and_reserve_examples(self):
        book = Book(1_000_000_000, {})
        self.assertIsNone(prequote(config(), book, buy()))
        self.assertEqual(prequote(config(), book, buy(lamports=80_000_001)), "risk_cap")
        small = Book(40_000_000, {})
        self.assertEqual(
            prequote(config(40_000_000, estimates={"buy1": BuyEstimate(7_000_000, 0)}), small, buy(lamports=3_200_000)),
            "risk_reserve",
        )
        self.assertIsNone(
            prequote(config(40_000_000, estimates={"buy1": BuyEstimate(6_800_000, 0)}), small, buy(lamports=3_200_000))
        )
        self.assertEqual(prequote(config(estimates={}), book, buy()), "risk_reserve")

    def test_positions_sells_denylist_and_unknown_mint(self):
        positions = {mint: position(mint) for mint in (MINT_A, MINT_B, MINT_C)}
        book = Book(1_000_000_000, positions)
        self.assertEqual(prequote(config(), book, buy(MINT_D, signal_id="buy4")), "risk_max_positions")
        self.assertEqual(prequote(config(), book, buy(MINT_A)), "risk_mint_open")
        self.assertEqual(prequote(config(), Book(1, {}), sell()), "risk_no_position")
        self.assertEqual(prequote(config(), Book(1, {MINT_A: position()}), sell(amount=999)), "risk_sell_size")
        self.assertIsNone(prequote(config(), Book(1, {MINT_A: position()}), sell(amount=1000)))
        denied = config(denylist=(MINT_DENIED,))
        self.assertEqual(prequote(denied, Book(denied.bank_lamports, {}), buy(MINT_DENIED)), "denylist")
        self.assertEqual(denylist_label(config()), "not_configured")
        self.assertEqual(denylist_label(config(denylist=())), "configured")
        self.assertEqual(prequote(config(), Book(1_000_000_000, {}), buy("not-a-listed-mint")), "unknown_mint")
        self.assertEqual(prequote(config(), Book(1, {}), buy(SOL_MINT)), "pair")

    def test_decimals_do_not_scale_base_units(self):
        book = Book(1_000_000_000, {MINT_A: position()})
        self.assertIsNone(prequote(config(mints={MINT_A: 0}), book, sell(amount=1000)))
        self.assertIsNone(prequote(config(mints={MINT_A: 9}), book, sell(amount=1000)))
        self.assertEqual(prequote(config(mints={MINT_A: 0}), book, sell(amount=999)), "risk_sell_size")


if __name__ == "__main__":
    unittest.main()
