import json
import tempfile
import unittest
from pathlib import Path

from executor.config import load_config
from executor.journal import CrashBeforeRename, load_records
from executor.run import StopFlag, run
from executor.signer import sign
from executor.types import SOL_MINT
from tests.fixtures import MINT_A, MINT_B, MINT_C, MINT_D, MINT_DENIED, sample_config


class FakeTransport:
    def __init__(self, out_amounts):
        self.out_amounts = list(out_amounts)
        self.calls = []

    def get(self, url, headers):
        self.calls.append((url, dict(headers)))
        amount = url.split("amount=")[1].split("&")[0]
        out_amount = self.out_amounts.pop(0)
        body = {
            "router": "metis",
            "inAmount": amount,
            "outAmount": out_amount,
            "transaction": None,
        }
        return 200, body


def write_config(directory: Path, estimates, bank="1000000000"):
    body = sample_config(
        bank_lamports=bank,
        buy_estimates={
            key: {"estimated_fee_lamports": str(fee), "estimated_rent_lamports": "0"}
            for key, fee in estimates.items()
        }
    )
    path = directory / "config.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return load_config(path)


def write_signal(inbox: Path, name: str, body: dict):
    inbox.mkdir(parents=True, exist_ok=True)
    path = inbox / name
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.inbox = self.root / "inbox"
        self.state = self.root / "state"

    def test_buy_sell_dedup_and_no_signer(self):
        config = write_config(self.root, {"buy1": 5000, "buy2": 5000})
        signal = {
            "id": "buy1",
            "side": "buy",
            "mint": MINT_A,
            "created_at": "2026-11-07T10:00:00+02:00",
            "size_sol": "0.08",
        }
        path = write_signal(self.inbox, "a.json", signal)
        before = path.read_bytes()
        transport = FakeTransport(["1000", "80000000", "70000000"])
        self.assertEqual(run(config, self.inbox, self.state, transport), 0)
        write_signal(
            self.inbox,
            "b.json",
            {
                "id": "sell1",
                "side": "sell",
                "mint": MINT_A,
                "created_at": "2026-11-07T10:01:00+02:00",
                "token_amount": "1000",
            },
        )
        self.assertEqual(run(config, self.inbox, self.state, transport), 0)
        self.assertEqual(run(config, self.inbox, self.state, transport), 0)
        self.assertEqual(len(transport.calls), 3)
        self.assertNotIn("taker", transport.calls[0][0])
        self.assertEqual(transport.calls[0][1], {})
        receipts, _marks = load_records(self.state)
        self.assertEqual(sorted(item.id for item in receipts), ["buy1", "sell1"])
        self.assertTrue(all(item.outcome == "quoted" for item in receipts))
        self.assertEqual(path.read_bytes(), before)
        called = {"value": False}

        def boom(payload):
            called["value"] = True
            raise AssertionError(payload)

        import executor.signer as signer_mod

        original = signer_mod.sign
        signer_mod.sign = boom
        try:
            self.assertEqual(run(config, self.inbox, self.state, transport), 0)
        finally:
            signer_mod.sign = original
        self.assertFalse(called["value"])
        with self.assertRaises(Exception):
            sign(b"nope")

    def test_risk_rejection_does_not_quote(self):
        config = write_config(self.root, {"buy1": 5000})
        write_signal(
            self.inbox,
            "a.json",
            {
                "id": "buy1",
                "side": "buy",
                "mint": MINT_A,
                "created_at": "2026-11-07T10:00:00+02:00",
                "size_sol": "0.09",
            },
        )
        transport = FakeTransport(["1000"])
        run(config, self.inbox, self.state, transport)
        self.assertEqual(transport.calls, [])
        receipts, _marks = load_records(self.state)
        self.assertEqual(receipts[0].reason, "risk_cap")
        self.assertEqual(receipts[0].denylist, "not_configured")

    def test_crash_before_rename_quotes_once_on_recovery(self):
        config = write_config(self.root, {"buy1": 5000})
        write_signal(
            self.inbox,
            "a.json",
            {
                "id": "buy1",
                "side": "buy",
                "mint": MINT_A,
                "created_at": "2026-11-07T10:00:00+02:00",
                "size_sol": "0.08",
            },
        )
        transport = FakeTransport(["1000", "1000", "80000000", "80000000"])
        with self.assertRaises(CrashBeforeRename):
            run(config, self.inbox, self.state, transport, crash_commits=1)
        self.assertEqual(len(transport.calls), 1)
        run(config, self.inbox, self.state, transport)
        receipts, _marks = load_records(self.state)
        self.assertEqual([item.id for item in receipts], ["buy1"])
        buy_quotes = [url for url, _headers in transport.calls if f"inputMint={SOL_MINT}" in url]
        self.assertEqual(len(buy_quotes), 2)
        run(config, self.inbox, self.state, transport)
        buy_quotes = [url for url, _headers in transport.calls if f"inputMint={SOL_MINT}" in url]
        self.assertEqual(len(buy_quotes), 2)

    def test_fourth_open_position_is_rejected(self):
        estimates = {f"buy{i}": 5000 for i in range(1, 6)}
        config = write_config(self.root, estimates)
        for index, mint in enumerate((MINT_A, MINT_B, MINT_C, MINT_D), start=1):
            write_signal(
                self.inbox,
                f"{index}.json",
                {
                    "id": f"buy{index}",
                    "side": "buy",
                    "mint": mint,
                    "created_at": f"2026-11-07T10:0{index}:00+02:00",
                    "size_sol": "0.01",
                },
            )
        transport = FakeTransport(["10", "10", "10", "10000000", "10000000", "10000000"])
        run(config, self.inbox, self.state, transport)
        receipts, _marks = load_records(self.state)
        reasons = {item.id: item.reason for item in receipts}
        self.assertEqual(reasons["buy4"], "risk_max_positions")
        buy_quotes = [url for url, _headers in transport.calls if f"inputMint={SOL_MINT}" in url]
        self.assertEqual(len(buy_quotes), 3)

    def test_pair_unknown_mint_unsafe_and_transport_failure(self):
        config = write_config(self.root, {"buy1": 5000, "buy9": 5000, "buy2": 5000})
        write_signal(
            self.inbox,
            "a.json",
            {
                "id": "buy1",
                "side": "buy",
                "mint": SOL_MINT,
                "created_at": "2026-11-07T10:00:00+02:00",
                "size_sol": "0.01",
            },
        )
        transport = FakeTransport([])
        run(config, self.inbox, self.state, transport)
        self.assertEqual(load_records(self.state)[0][0].reason, "pair")
        self.assertEqual(transport.calls, [])
        write_signal(
            self.inbox,
            "b.json",
            {
                "id": "buy9",
                "side": "buy",
                "mint": MINT_DENIED,
                "created_at": "2026-11-07T10:02:00+02:00",
                "size_sol": "0.01",
            },
        )
        run(config, self.inbox, self.state, transport)
        reasons = {item.id: item.reason for item in load_records(self.state)[0]}
        self.assertEqual(reasons["buy9"], "unknown_mint")
        self.assertEqual(transport.calls, [])

    def test_quote_unsafe_is_terminal_and_timeout_is_not(self):
        config = write_config(self.root, {"buy1": 5000, "buy2": 5000})
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

        class Unsafe:
            def __init__(self):
                self.calls = []

            def get(self, url, headers):
                self.calls.append(url)
                amount = url.split("amount=")[1].split("&")[0]
                return 200, {"transaction": "AQID", "inAmount": amount, "outAmount": "10"}

        unsafe = Unsafe()
        run(config, self.inbox, self.state, unsafe)
        run(config, self.inbox, self.state, unsafe)
        self.assertEqual(len(unsafe.calls), 1)
        self.assertEqual(load_records(self.state)[0][0].reason, "quote_unsafe")
        journal_text = "\n".join(path.read_text(encoding="utf-8") for path in (self.state / "journal").rglob("*.json"))
        self.assertNotIn("AQID", journal_text)

        class Down:
            def __init__(self):
                self.calls = []

            def get(self, url, headers):
                self.calls.append(url)
                raise TimeoutError("slow")

        write_signal(
            self.inbox,
            "c.json",
            {
                "id": "buy2",
                "side": "buy",
                "mint": MINT_B,
                "created_at": "2026-11-07T10:03:00+02:00",
                "size_sol": "0.01",
            },
        )
        down = Down()
        run(config, self.inbox, self.state, down)
        self.assertEqual(len(down.calls), 1)
        self.assertFalse(any(item.id == "buy2" for item in load_records(self.state)[0]))

    def test_reopen_after_a_full_close(self):
        config = write_config(self.root, {"buy1": 5000, "buy2": 5000})
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
                "id": "sell1",
                "side": "sell",
                "mint": MINT_A,
                "created_at": "2026-11-07T10:01:00+02:00",
                "token_amount": "10",
            },
        )
        transport = FakeTransport(["10", "5000000", "10", "10000000"])
        run(config, self.inbox, self.state, transport)
        self.assertEqual(len(transport.calls), 2)
        write_signal(
            self.inbox,
            "c.json",
            {
                "id": "buy2",
                "side": "buy",
                "mint": MINT_A,
                "created_at": "2026-11-07T10:02:00+02:00",
                "size_sol": "0.01",
            },
        )
        run(config, self.inbox, self.state, transport)
        self.assertEqual(len(transport.calls), 4)
        quoted = {item.id for item in load_records(self.state)[0] if item.outcome == "quoted"}
        self.assertEqual(quoted, {"buy1", "sell1", "buy2"})

    def test_stop_commits_one_exit_and_no_mark_file(self):
        config = write_config(self.root, {"buy1": 0}, bank="2000000000")
        write_signal(
            self.inbox,
            "a.json",
            {
                "id": "buy1",
                "side": "buy",
                "mint": MINT_A,
                "created_at": "2026-11-07T10:00:00+02:00",
                "size_sol": "0.1",
            },
        )
        transport = FakeTransport(["1000000000", "94000000"])
        run(config, self.inbox, self.state, transport)
        receipts, marks = load_records(self.state)
        exits = [item for item in receipts if item.trigger == "stop"]
        self.assertEqual([item.id for item in exits], ["rexit.buy1.stop"])
        self.assertIsNone(exits[0].reason)
        self.assertEqual(exits[0].outcome, "quoted")
        self.assertEqual(marks, [])
        calls = len(transport.calls)
        run(config, self.inbox, self.state, transport)
        self.assertEqual(len(transport.calls), calls)

    def test_arm_then_giveback(self):
        config = write_config(self.root, {"buy1": 0}, bank="2000000000")
        write_signal(
            self.inbox,
            "a.json",
            {
                "id": "buy1",
                "side": "buy",
                "mint": MINT_A,
                "created_at": "2026-11-07T10:00:00+02:00",
                "size_sol": "0.1",
            },
        )
        transport = FakeTransport(["1000000000", "112000000", "120000000", "112800000"])
        run(config, self.inbox, self.state, transport)
        _receipts, marks = load_records(self.state)
        self.assertEqual([item.n for item in marks], [1])
        run(config, self.inbox, self.state, transport)
        _receipts, marks = load_records(self.state)
        self.assertEqual([item.n for item in marks], [1, 2])
        run(config, self.inbox, self.state, transport)
        receipts, marks = load_records(self.state)
        self.assertEqual([item.n for item in marks], [1, 2])
        self.assertEqual([item.id for item in receipts if item.trigger == "trailing"], ["rexit.buy1.trailing"])
        calls = len(transport.calls)
        run(config, self.inbox, self.state, transport)
        self.assertEqual(len(transport.calls), calls)

    def test_stop_wins_after_the_peak(self):
        config = write_config(self.root, {"buy1": 0}, bank="2000000000")
        write_signal(
            self.inbox,
            "a.json",
            {
                "id": "buy1",
                "side": "buy",
                "mint": MINT_A,
                "created_at": "2026-11-07T10:00:00+02:00",
                "size_sol": "0.1",
            },
        )
        transport = FakeTransport(["1000000000", "120000000", "94000000"])
        run(config, self.inbox, self.state, transport)
        run(config, self.inbox, self.state, transport)
        receipts, _marks = load_records(self.state)
        self.assertEqual([item.id for item in receipts if item.side == "sell"], ["rexit.buy1.stop"])

    def test_unsafe_mark_commits_nothing_and_can_retry(self):
        config = write_config(self.root, {"buy1": 0}, bank="2000000000")
        write_signal(
            self.inbox,
            "a.json",
            {
                "id": "buy1",
                "side": "buy",
                "mint": MINT_A,
                "created_at": "2026-11-07T10:00:00+02:00",
                "size_sol": "0.1",
            },
        )

        class OnceUnsafe:
            def __init__(self):
                self.calls = 0

            def get(self, url, headers):
                self.calls += 1
                amount = url.split("amount=")[1].split("&")[0]
                if self.calls == 1:
                    return 200, {"transaction": None, "inAmount": amount, "outAmount": "1000000000", "router": "metis"}
                return 200, {"transaction": "AQID", "inAmount": amount, "outAmount": "1000000000"}

        transport = OnceUnsafe()
        run(config, self.inbox, self.state, transport)
        receipts, marks = load_records(self.state)
        self.assertEqual([item.outcome for item in receipts], ["quoted"])
        self.assertEqual(marks, [])
        journal_text = "\n".join(path.read_text(encoding="utf-8") for path in (self.state / "journal").rglob("*.json"))
        self.assertNotIn("AQID", journal_text)
        run(config, self.inbox, self.state, transport)
        self.assertGreater(transport.calls, 2)


if __name__ == "__main__":
    unittest.main()
