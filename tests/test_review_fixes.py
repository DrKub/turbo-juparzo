import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request

from executor.book import fold
from executor.cli import bound_state
from executor.journal import JournalError, commit_record, load_records
from executor.lock import LockError, exclusive_lock
from executor.quote import urlopen
from executor.records import receipt_payload
from executor.run import run
from executor.types import Receipt, StoredQuote
from tests.fixtures import MINT_A
from tests.test_run import FakeTransport, write_config, write_signal


def quoted_sell(seq=1):
    return Receipt(
        "sell-orphan",
        "sell",
        "quoted",
        "signal",
        MINT_A,
        "1000",
        "70000000",
        None,
        "not_configured",
        seq,
        None,
        None,
        None,
        StoredQuote("metis", "1000", "70000000", None, None, None),
    )


def quoted_buy(seq=1, signal_id="buy1"):
    return receipt_payload(
        Receipt(
            signal_id,
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


class JournalIntegrityTest(unittest.TestCase):
    def test_orphan_sell_does_not_credit_the_bank(self):
        with self.assertRaises(JournalError):
            fold(1_000_000_000, [quoted_sell()], [])

    def test_alias_and_duplicate_receipt_halt(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        relative = Path("journal") / "buy1" / "buy.json"
        commit_record(root, relative, quoted_buy())
        original = root / relative
        alias = original.parent / "alias.json"
        os.link(original, alias)
        with self.assertRaises(JournalError):
            load_records(root)
        alias.unlink()
        link = original.parent / "link.json"
        os.symlink(original.name, link)
        with self.assertRaises(JournalError):
            load_records(root)

    def test_directory_fsync_reaches_the_state_root(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        fsynced = []
        real_fsync = os.fsync

        def tracking(fd):
            fsynced.append(os.readlink(f"/proc/self/fd/{fd}"))
            return real_fsync(fd)

        os.fsync = tracking
        try:
            commit_record(root, Path("journal") / "buy1" / "marks" / "1.json", {
                "record_type": "jupiter_dry_run_mark",
                "buy_id": "buy1",
                "n": "1",
                "mark_out_lamports": "1",
                "mark_in_base_units": "1",
                "seq": "1",
                "quote": {"router": None, "inAmount": "1", "outAmount": "1", "feeBps": None, "feeMint": None, "platformFee": None},
            })
        finally:
            os.fsync = real_fsync
        names = [Path(item).name for item in fsynced]
        self.assertIn(root.name, names)
        self.assertIn("journal", names)
        self.assertIn("buy1", names)
        self.assertIn("marks", names)


class InboxConflictTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.inbox = self.root / "inbox"
        self.state = self.root / "state"

    def test_malformed_duplicate_does_not_burn_the_valid_signal(self):
        config = write_config(self.root, {"buy1": 5000})
        write_signal(
            self.inbox,
            "later-bad.json",
            {
                "id": "buy1",
                "side": "buy",
                "mint": MINT_A,
                "created_at": "2026-11-07T11:00:00+02:00",
                "size_sol": "0.01",
                "note": "extra",
            },
        )
        write_signal(
            self.inbox,
            "earlier-good.json",
            {
                "id": "buy1",
                "side": "buy",
                "mint": MINT_A,
                "created_at": "2026-11-07T10:00:00+02:00",
                "size_sol": "0.01",
            },
        )
        transport = FakeTransport(["10", "10000000"])
        self.assertEqual(run(config, self.inbox, self.state, transport), 0)
        receipts, _marks = load_records(self.state)
        quoted = [item for item in receipts if item.id == "buy1" and item.outcome == "quoted"]
        self.assertEqual(len(quoted), 1)
        self.assertGreaterEqual(len(transport.calls), 1)

    def test_conflicting_valid_signals_halt_without_a_receipt(self):
        config = write_config(self.root, {"buy1": 5000})
        write_signal(
            self.inbox,
            "a.json",
            {
                "id": "buy1",
                "side": "buy",
                "mint": MINT_A,
                "created_at": "2026-11-07T10:00:00+02:00",
                "size_sol": "0.01",
            },
        )
        write_signal(
            self.inbox,
            "b.json",
            {
                "id": "buy1",
                "side": "buy",
                "mint": MINT_A,
                "created_at": "2026-11-07T10:05:00+02:00",
                "size_sol": "0.02",
            },
        )
        transport = FakeTransport(["10"])
        self.assertEqual(run(config, self.inbox, self.state, transport), 4)
        self.assertEqual(transport.calls, [])
        self.assertFalse((self.state / "journal").exists())

    def test_earlier_sell_does_not_close_a_later_buy(self):
        config = write_config(self.root, {"b-buy": 5000})
        write_signal(
            self.inbox,
            "sell.json",
            {
                "id": "a-sell",
                "side": "sell",
                "mint": MINT_A,
                "created_at": "2026-11-07T10:00:00+02:00",
                "token_amount": "10",
            },
        )
        write_signal(
            self.inbox,
            "buy.json",
            {
                "id": "b-buy",
                "side": "buy",
                "mint": MINT_A,
                "created_at": "2026-11-07T10:01:00+02:00",
                "size_sol": "0.01",
            },
        )
        transport = FakeTransport(["10", "10000000"])
        self.assertEqual(run(config, self.inbox, self.state, transport), 0)
        receipts, _marks = load_records(self.state)
        by_id = {item.id: item for item in receipts}
        self.assertEqual(by_id["a-sell"].reason, "risk_no_position")
        self.assertEqual(by_id["b-buy"].outcome, "quoted")


class LockBoundaryTest(unittest.TestCase):
    def test_default_state_follows_the_inbox_not_the_cwd(self):
        inbox = Path("/tmp/same-inbox")
        self.assertEqual(
            bound_state(inbox, None),
            Path("/tmp/data/jupiter-dry-runs"),
        )
        self.assertEqual(bound_state(inbox, None), bound_state(inbox, None))

    def test_unlinked_lock_is_a_new_inode(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "state" / "executor.lock"
        with exclusive_lock(path) as held:
            os.unlink(path)
            with exclusive_lock(path) as replaced:
                self.assertNotEqual(os.fstat(held).st_ino, os.fstat(replaced).st_ino)

    def test_symlink_lock_is_refused(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        state = root / "state"
        state.mkdir()
        target = root / "elsewhere"
        target.write_text("", encoding="utf-8")
        os.symlink(target, state / "executor.lock")
        with self.assertRaises(LockError):
            with exclusive_lock(state / "executor.lock"):
                pass


class RedirectTest(unittest.TestCase):
    def test_local_http_302_is_refused(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                location = f"http://127.0.0.1:{self.server.server_address[1]}/execute"
                self.send_header("Location", location)
                self.end_headers()

            def log_message(self, fmt, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/order"
            with self.assertRaises(OSError):
                urlopen(Request(url, method="GET"), timeout=2)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
