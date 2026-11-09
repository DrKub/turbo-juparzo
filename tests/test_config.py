import json
import tempfile
import unittest
from pathlib import Path

from executor.config import ConfigError, is_mint, load_config
from executor.types import SOL_MINT
from tests.fixtures import MINT_A, MINT_DENIED, sample_config


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def write(self, body: dict) -> Path:
        path = self.dir / "config.json"
        path.write_text(json.dumps(body), encoding="utf-8")
        return path

    def test_fixture_mints_are_32_bytes(self):
        self.assertTrue(is_mint(MINT_A))
        self.assertTrue(is_mint(MINT_DENIED))
        self.assertTrue(is_mint(SOL_MINT))
        self.assertFalse(is_mint("ECR"))

    def test_absent_denylist_and_estimates_have_distinct_empty_states(self):
        loaded = load_config(self.write(sample_config()))
        self.assertEqual(loaded.bank_lamports, 1_000_000_000)
        self.assertIsNone(loaded.denylist_mints)
        self.assertEqual(loaded.buy_estimates, {})
        self.assertEqual(loaded.mint_metadata[MINT_A], 6)

    def test_empty_denylist_is_configured(self):
        loaded = load_config(self.write(sample_config(denylist_mints=[])))
        self.assertEqual(loaded.denylist_mints, ())

    def test_unknown_key_bad_bank_and_symbol_denylist_are_config_errors(self):
        for body in (
            sample_config(mode="live"),
            sample_config(bank_lamports="0"),
            sample_config(bank_lamports=100),
            sample_config(denylist_mints=["ECR"]),
            sample_config(mint_metadata=[{"mint": MINT_A, "decimals": True}]),
            sample_config(mint_metadata=[{"mint": MINT_A, "decimals": 256}]),
            sample_config(buy_estimates={"buy1": {"estimated_fee_lamports": "1"}}),
        ):
            with self.assertRaises(ConfigError):
                load_config(self.write(body))


if __name__ == "__main__":
    unittest.main()
