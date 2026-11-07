import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "metis-offline"


class LauncherTest(unittest.TestCase):
    def test_missing_venv_fails_without_installing(self):
        text = LAUNCHER.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "metis-offline"
            dest.write_text(text, encoding="utf-8")
            dest.chmod(dest.stat().st_mode | stat.S_IXUSR)
            proc = subprocess.run([str(dest), "anything.json"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "")
        self.assertIn("requirements-metis-offline.txt", proc.stderr)
        self.assertIn("does not install", proc.stderr)
        self.assertIn(".venv-metis-offline", proc.stderr)


if __name__ == "__main__":
    unittest.main()
