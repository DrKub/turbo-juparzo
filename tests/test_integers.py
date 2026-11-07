import unittest

from executor.integers import parse_nonneg, parse_pos, parse_size_lamports


class IntegersTest(unittest.TestCase):
    def test_size_sol_converts_without_rounding(self):
        self.assertEqual(parse_size_lamports("0.08"), 80_000_000)
        self.assertEqual(parse_size_lamports("1"), 1_000_000_000)
        self.assertEqual(parse_size_lamports("0.000000001"), 1)
        self.assertIsNone(parse_size_lamports("0.0000000001"))
        self.assertIsNone(parse_size_lamports("0"))
        self.assertIsNone(parse_size_lamports("0.0"))
        self.assertIsNone(parse_size_lamports("01"))
        self.assertIsNone(parse_size_lamports("1e-3"))
        self.assertIsNone(parse_size_lamports("-1"))
        self.assertIsNone(parse_size_lamports(0.08))

    def test_canonical_integers(self):
        self.assertEqual(parse_nonneg("0"), 0)
        self.assertEqual(parse_pos("80000000"), 80_000_000)
        self.assertIsNone(parse_pos("0"))
        self.assertIsNone(parse_nonneg("00"))
        self.assertIsNone(parse_nonneg(5))


if __name__ == "__main__":
    unittest.main()
