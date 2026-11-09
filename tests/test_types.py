import unittest

from executor.types import ORDER_URL, SMOKE_AMOUNT, SOL_MINT, USDC_MINT
from tests.fixtures import MINT_A, MINT_DENIED


class TypesTest(unittest.TestCase):
    def test_constants_match_the_spec(self):
        self.assertEqual(SOL_MINT, "So11111111111111111111111111111111111111112")
        self.assertEqual(ORDER_URL, "https://api.jup.ag/swap/v2/order")
        self.assertEqual(USDC_MINT, "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
        self.assertEqual(SMOKE_AMOUNT, "100000000")
        self.assertNotEqual(MINT_A, MINT_DENIED)


if __name__ == "__main__":
    unittest.main()
