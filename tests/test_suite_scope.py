import unittest
from pathlib import Path

from executor.quote import order_url
from executor.smoke import smoke_request


class SuiteScopeTest(unittest.TestCase):
    def test_default_suite_does_not_call_smoke_or_open_a_socket(self):
        root = Path(__file__).resolve().parents[1]
        tests = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (root / "tests").rglob("test_*.py")
            if path.name != "test_suite_scope.py"
        )
        self.assertNotIn("smoke.main(", tests)
        for path in (root / "executor").rglob("*.py"):
            if path.name in {"quote.py", "smoke.py"}:
                continue
            self.assertNotIn("urlopen", path.read_text(encoding="utf-8"))
        url = order_url(smoke_request())
        self.assertIn("amount=100000000", url)
        self.assertNotIn("taker", url)
        self.assertTrue(url.startswith("https://api.jup.ag/swap/v2/order?"))


if __name__ == "__main__":
    unittest.main()
