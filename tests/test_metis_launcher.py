import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "metis-offline"


class LauncherTest(unittest.TestCase):
    def test_launcher_needs_no_venv_or_third_party_packages(self):
        text = LAUNCHER.read_text(encoding="utf-8")
        self.assertNotIn(".venv", text)
        self.assertNotIn("pip", text)
        self.assertIn("python3", text)

    def test_missing_interpreter_fails_without_installing(self):
        text = LAUNCHER.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "metis-offline"
            dest.write_text(text, encoding="utf-8")
            dest.chmod(dest.stat().st_mode | stat.S_IXUSR)
            env = {**os.environ, "METIS_PYTHON": "definitely-not-a-python-binary"}
            proc = subprocess.run([str(dest), "anything.json"], capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "")
        self.assertIn("does not install", proc.stderr)

    def test_missing_file_is_a_request_failure(self):
        proc = subprocess.run([str(LAUNCHER), "no-such-request.json"], capture_output=True, text=True, cwd=tempfile.gettempdir())
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "")
        self.assertIn("no such file", proc.stderr)


if __name__ == "__main__":
    unittest.main()
