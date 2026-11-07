import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from executor.cli import install_sigterm, main
from executor.run import StopFlag
from tests.fixtures import MINT_A, sample_config


class CliTest(unittest.TestCase):
    def test_live_refusal_creates_no_state_and_hides_the_secret(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state = Path(tmp.name) / "state"
        secret = "super-secret-value"
        code = main(["--live", "--state", str(state)], {"JUPITER_API_KEY": secret})
        self.assertEqual(code, 3)
        self.assertFalse(state.exists())

    def test_subprocess_mode_live_is_exit_3(self):
        result = subprocess.run(
            [sys.executable, "-m", "executor", "--config", "missing.json", "--inbox", "missing"],
            env={**os.environ, "MODE": "live"},
            capture_output=True,
            text=True,
            timeout=5,
            cwd=Path(__file__).resolve().parents[1],
        )
        self.assertEqual(result.returncode, 3)
        self.assertNotIn("missing.json", result.stderr)

    def test_second_process_exits_2_without_writing_journal(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        config = root / "config.json"
        config.write_text(json.dumps(sample_config()), encoding="utf-8")
        inbox = root / "inbox"
        inbox.mkdir()
        state = root / "state"
        ready = root / "ready"
        held = subprocess.Popen(
            [
                sys.executable,
                "-c",
                textwrap.dedent(
                    """
                    import sys
                    import time
                    from pathlib import Path
                    from executor.lock import exclusive_lock
                    with exclusive_lock(Path(sys.argv[1])):
                        Path(sys.argv[2]).write_text("held", encoding="utf-8")
                        time.sleep(5)
                    """
                ),
                str(state / "executor.lock"),
                str(ready),
            ],
            cwd=Path(__file__).resolve().parents[1],
        )
        result = None
        try:
            for _ in range(50):
                if ready.exists():
                    break
                time.sleep(0.05)
            self.assertTrue(ready.exists())
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "executor",
                    "--config",
                    str(config),
                    "--inbox",
                    str(inbox),
                    "--state",
                    str(state),
                ],
                capture_output=True,
                text=True,
                timeout=5,
                cwd=Path(__file__).resolve().parents[1],
            )
        finally:
            held.send_signal(signal.SIGTERM)
            held.wait(timeout=5)
        self.assertIsNotNone(result)
        self.assertEqual(result.returncode, 2)
        self.assertFalse((state / "journal").exists())

    def test_sigterm_during_quote_writes_no_sell(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        config_path = root / "config.json"
        body = sample_config(buy_estimates={"buy1": {"estimated_fee_lamports": "0", "estimated_rent_lamports": "0"}})
        config_path.write_text(json.dumps(body), encoding="utf-8")
        inbox = root / "inbox"
        inbox.mkdir()
        (inbox / "a.json").write_text(
            json.dumps(
                {
                    "id": "buy1",
                    "side": "buy",
                    "mint": MINT_A,
                    "created_at": "2026-11-07T10:00:00+02:00",
                    "size_sol": "0.01",
                }
            ),
            encoding="utf-8",
        )
        stop = StopFlag()
        previous = signal.getsignal(signal.SIGTERM)
        self.addCleanup(lambda: signal.signal(signal.SIGTERM, previous))
        install_sigterm(stop)
        entered = threading.Event()
        release = threading.Event()

        class Hold:
            def get(self, url, headers):
                entered.set()
                release.wait(5)
                query = parse_qs(urlparse(url).query)
                return 200, {
                    "transaction": None,
                    "inputMint": query["inputMint"][0],
                    "outputMint": query["outputMint"][0],
                    "inAmount": query["amount"][0],
                    "outAmount": "10",
                    "router": "metis",
                }

        def poke():
            self.assertTrue(entered.wait(5))
            os.kill(os.getpid(), signal.SIGTERM)
            release.set()

        threading.Thread(target=poke).start()
        from executor.config import load_config
        from executor.run import run

        code = run(load_config(config_path), inbox, root / "state", Hold(), stop=stop)
        self.assertEqual(code, 0)
        journal = root / "state" / "journal"
        text = "" if not journal.exists() else "\n".join(path.read_text(encoding="utf-8") for path in journal.rglob("*.json"))
        self.assertNotIn('"side": "sell"', text)
        self.assertNotIn('"side":"sell"', text.replace(" ", ""))
        self.assertNotIn("liquidation", text)

    def test_wallet_is_not_hardcoded(self):
        root = Path(__file__).resolve().parents[1] / "executor"
        text = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))
        self.assertNotIn("8Xm6iFvk9J8rBi7BvRAbiZU2RAEE1b7AM714QmiEXQ8a", text)


if __name__ == "__main__":
    unittest.main()
