import io
import unittest
from contextlib import redirect_stderr

from executor.schema import log_schema, parse_signal
from executor.types import SchemaRejection, Signal
from tests.fixtures import MINT_A


def buy(**extra):
    body = {
        "id": "buy1",
        "side": "buy",
        "mint": MINT_A,
        "created_at": "2026-11-07T10:00:00+02:00",
        "size_sol": "0.08",
    }
    body.update(extra)
    return body


class SchemaTest(unittest.TestCase):
    def test_buy_and_sell_field_sets(self):
        parsed = parse_signal(buy())
        self.assertIsInstance(parsed, Signal)
        self.assertEqual(parsed.size_lamports, 80_000_000)
        self.assertIsNone(parsed.token_amount)
        sell = parse_signal(
            {
                "id": "sell1",
                "side": "sell",
                "mint": MINT_A,
                "created_at": "2026-11-07T10:01:00Z",
                "token_amount": "1000",
            }
        )
        self.assertIsInstance(sell, Signal)
        self.assertEqual(sell.token_amount, 1000)
        self.assertLess(parsed.sort_key, sell.sort_key)

    def test_rejects_unknown_fields_zero_and_unsafe_ids_without_logging_the_body(self):
        cases = [
            buy(note="secret-body"),
            buy(size_sol="0"),
            buy(token_amount="1"),
            buy(id="rexit.buy1.stop"),
            buy(created_at="2026-11-07T10:00:00"),
            {"id": "bad id", "side": "buy"},
        ]
        for body in cases:
            parsed = parse_signal(body)
            self.assertIsInstance(parsed, SchemaRejection)
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            log_schema(parse_signal(buy(note="secret-body")))
        self.assertNotIn("secret-body", stderr.getvalue())
        self.assertIn("schema buy1", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
