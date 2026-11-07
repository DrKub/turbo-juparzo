import tempfile
import unittest
from pathlib import Path

from executor.journal import CrashBeforeRename, commit_record, discard_temps, load_records, receipt_path
from executor.records import receipt_payload
from executor.types import Receipt, StoredQuote
from tests.fixtures import MINT_A


def payload(seq=1):
    return receipt_payload(
        Receipt(
            "buy1",
            "buy",
            "quoted",
            "signal",
            MINT_A,
            "80000000",
            "1000",
            None,
            "not_configured",
            seq,
            80_000_000,
            5000,
            0,
            StoredQuote("metis", "80000000", "1000", None, None, None),
        )
    )


class JournalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_rename_is_durable_and_temp_without_rename_is_discarded(self):
        relative = receipt_path("buy1", "buy")
        with self.assertRaises(CrashBeforeRename):
            commit_record(self.root, relative, payload(), crash=True)
        self.assertFalse((self.root / relative).exists())
        self.assertTrue(list(self.root.rglob("*.tmp")))
        discard_temps(self.root)
        self.assertFalse(list(self.root.rglob("*.tmp")))
        commit_record(self.root, relative, payload())
        loaded, marks = load_records(self.root)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].seq, 1)
        self.assertEqual(marks, [])
        self.assertFalse(list(self.root.rglob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
