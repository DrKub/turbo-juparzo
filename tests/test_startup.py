import unittest

from executor.startup import find_startup_refusal


class StartupTest(unittest.TestCase):
    def test_live_and_mandate_refuse_without_echoing_secrets(self):
        secret = "super-secret-value"
        cases = [
            (["--live"], {}, "refused: live"),
            ([], {"MODE": "live"}, "refused: live"),
            ([], {"LIVE": "1"}, "refused: live"),
            ([], {"DRY_RUN": "0"}, "refused: live"),
            ([], {"DRY_RUN": "false"}, "refused: live"),
            (["--mandate", "later.json"], {}, "refused: mandate"),
            ([], {"MANDATE_EXPIRY": "2026-11-08T00:00:00+02:00"}, "refused: mandate"),
            ([], {"JUPITER_API_KEY": secret}, "refused: live"),
            ([], {"TAKER": "wallet"}, "refused: live"),
            ([], {"EXECUTE_URL": "https://api.jup.ag/swap/v2/execute"}, "refused: live"),
            (["--build-url", "https://api.jup.ag/swap/v2/build"], {}, "refused: live"),
            (["--mystery"], {}, "refused: config"),
        ]
        for argv, env, expected in cases:
            message = find_startup_refusal(argv, env)
            self.assertEqual(message, expected)
            self.assertNotIn(secret, message)

    def test_dry_run_invocation_has_no_refusal(self):
        argv = ["--config", "cfg.json", "--inbox", "inbox", "--state", "state"]
        self.assertIsNone(find_startup_refusal(argv, {}))
        self.assertIsNone(find_startup_refusal(argv, {"DRY_RUN": "1", "MODE": "dry_run"}))


if __name__ == "__main__":
    unittest.main()
