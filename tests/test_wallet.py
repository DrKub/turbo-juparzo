"""Wallet profile config: single source of the signing address."""

import json
import tempfile
import unittest
from pathlib import Path

from tools import bridge, jupiter_unsigned_build as build_mod, sign_gate, wallet
from tests.test_sign_gate import NOW, FakeRpc, write_build  # noqa: F401

PHANTOM = "8Xm6iFvk9J8rBi7BvRAbiZU2RAEE1b7AM714QmiEXQ8a"
TURNKEY = "9Ao3ggbuUaiWPzYKr4VdsNQ9LMzM6MPmraZRVv4gv1UW"


class WalletProfileTest(unittest.TestCase):
    def test_default_is_phantom_and_unchanged(self):
        p = wallet.load_profile(env={})
        self.assertEqual((p.name, p.address, p.signer), ("phantom", PHANTOM, "phantom_embedded"))
        self.assertEqual(build_mod.USER, PHANTOM)
        self.assertEqual(bridge.USER, PHANTOM)

    def test_turnkey_profile_and_atas(self):
        p = wallet.load_profile("turnkey", env={})
        self.assertEqual((p.address, p.signer), (TURNKEY, "turnkey"))
        self.assertEqual(p.wsol_ata, "2cvHExWS1E1EPgTrNnfxo3eAtFepfZ8cBZW2xamSoYPM")
        self.assertEqual(p.usdc_ata, "3hQu4N3TECymZCPeZeq7X1vMkic8APDJvjnpUouAaFqZ")
        self.assertEqual(wallet.load_profile(env={"TURBO_WALLET": "turnkey"}).address, TURNKEY)
        self.assertEqual(wallet.load_profile("phantom", env={"TURBO_WALLET": "turnkey"}).address, PHANTOM)

    def test_bad_profiles_fail_closed(self):
        doc = json.loads(wallet.CONFIG_PATH.read_text())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "w.json"
            cases = {
                "unknown": ("nope", doc),
                "ata": ("turnkey", {**doc, "profiles": {"turnkey": {**doc["profiles"]["turnkey"], "wsol_ata": doc["profiles"]["phantom"]["wsol_ata"]}}}),
                "curve": ("turnkey", {**doc, "profiles": {"turnkey": {**doc["profiles"]["turnkey"], "address": "2cvHExWS1E1EPgTrNnfxo3eAtFepfZ8cBZW2xamSoYPM"}}}),
                "short": ("turnkey", {**doc, "profiles": {"turnkey": {**doc["profiles"]["turnkey"], "address": "abc"}}}),
                "signer": ("turnkey", {**doc, "profiles": {"turnkey": {**doc["profiles"]["turnkey"], "signer": "hot_key"}}}),
            }
            for label, (name, content) in cases.items():
                with self.subTest(label=label):
                    path.write_text(json.dumps(content))
                    with self.assertRaises(wallet.WalletConfigError):
                        wallet.load_profile(name, path=path, env={})

    def test_bridge_uses_selected_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = bridge.Bridge(state_dir=Path(tmp) / "s", log_path=Path(tmp) / "l", wallet_profile="turnkey")
            self.assertEqual(b.wallet.address, TURNKEY)
            with self.assertRaises(wallet.WalletConfigError):
                bridge.Bridge(state_dir=Path(tmp) / "s", wallet_profile="nope")

    def test_gate_expected_user_mismatch_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            write_build(d)  # synthetic fixture user, neither phantom nor turnkey
            code, result = sign_gate.run(d, log_path=d / "l", rpc=FakeRpc(), now=lambda: NOW, state_dir=d / "g", expected_user=TURNKEY)
            self.assertEqual((code, result["reason"]), (2, "wallet_mismatch"))
            code, result = sign_gate.run(d, log_path=d / "l", rpc=FakeRpc(), now=lambda: NOW, state_dir=d / "g")
            self.assertEqual(code, 0)

    def test_policies_filled_with_turnkey_addresses(self):
        text = (wallet.ROOT / "config" / "turnkey" / "policies.json").read_text()
        for placeholder in ("<TK_SIGNER>", "<TK_WSOL_ATA>", "<TK_USDC_ATA>"):
            self.assertNotIn(placeholder, text)
        p = wallet.load_profile("turnkey", env={})
        for value in (p.address, p.wsol_ata, p.usdc_ata):
            self.assertIn(value, text)
        self.assertIn("<API_USER_ID>", text)
        self.assertNotIn(PHANTOM, text)
        cfg = json.loads((wallet.ROOT / "config" / "turnkey" / "signer.json").read_text())
        self.assertEqual((cfg["enabled"], cfg["transport"], cfg["broadcast"]), (False, "none", False))


if __name__ == "__main__":
    unittest.main()
