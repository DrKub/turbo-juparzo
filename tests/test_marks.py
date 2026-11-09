import unittest
from dataclasses import replace

from executor.marks import apply_observation, decide_mark
from executor.types import Position
from tests.fixtures import MINT_A


def open_position():
    return Position(
        "buy1",
        MINT_A,
        100_000_000,
        1_000_000_000,
        1_000_000_000,
        100_000_000,
        1_000_000_000,
        False,
    )


class MarksTest(unittest.TestCase):
    def test_thresholds_use_exact_cross_multiplication(self):
        position = open_position()
        self.assertEqual(decide_mark(position, 94_000_000, 1_000_000_000), "stop")
        self.assertEqual(decide_mark(position, 94_000_001, 1_000_000_000), "observe")
        armed = apply_observation(position, 112_000_000, 1_000_000_000)
        self.assertTrue(armed.armed)
        self.assertEqual(armed.high_out_lamports, 112_000_000)
        peak = apply_observation(armed, 120_000_000, 1_000_000_000)
        self.assertEqual(decide_mark(peak, 112_800_000, 1_000_000_000), "trailing")
        self.assertEqual(decide_mark(peak, 112_800_001, 1_000_000_000), "observe")
        crashed = replace(peak, armed=True)
        self.assertEqual(decide_mark(crashed, 94_000_000, 1_000_000_000), "stop")
        midway = apply_observation(position, 110_000_000, 1_000_000_000)
        self.assertFalse(midway.armed)
        self.assertEqual(decide_mark(midway, 110_000_000, 1_000_000_000), "observe")


if __name__ == "__main__":
    unittest.main()
