import tempfile
import unittest
from pathlib import Path

from executor.lock import LockBusy, exclusive_lock


class LockTest(unittest.TestCase):
    def test_second_acquire_fails_and_a_released_lock_can_be_taken(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "executor.lock"
        with exclusive_lock(path):
            with self.assertRaises(LockBusy):
                with exclusive_lock(path):
                    pass
        with exclusive_lock(path):
            self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
