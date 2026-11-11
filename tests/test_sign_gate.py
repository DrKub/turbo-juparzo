"""Sign gate, operation log and result recorder. Network is always mocked."""

import base64
import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path

from metis_offline import fixtures as fx
from metis_offline.base58 import b58encode
from metis_offline.cli import public_report
from metis_offline.preflight import preflight
from tools import oplog, record_result, sign_gate

NOW = 1_800_000_000.0


class FakeRpc:
    def __init__(self, height=900, valid=True, tx=None):
        self.height, self.valid, self.tx, self.calls = height, valid, tx, []

    def __call__(self, method, params):
        self.calls.append(method)
        if method == "getBlockHeight":
            return self.height
        if method == "isBlockhashValid":
            return {"context": {"slot": 1}, "value": self.valid}
        if method == "getTransaction":
            return self.tx
        raise AssertionError(f"unexpected RPC {method}")


def write_build(directory: Path, case: fx.Case | None = None, **overrides) -> dict:
    case = case or fx.Case()
    request = case.request()
    report = public_report(preflight(case.raw(), case.intent, case.evidence()))
    sha = report["message_sha256"]
    summary = {
        "build": {"last_valid_block_height": 1_000, "blockhash": b58encode(fx.BLOCKHASH)},
        "metis_offline": {"exit": 0 if report["kind"] == "semantic_pass" else 2, "report": report},
        "simulation": {"message_sha256": sha, "err": None, "persistent_accounts_created": [], "wsol_ata_exists_after": False,
                       "usdc_raw_delta": 85_900, "units_consumed": 60_000},
        "finished_at_epoch": NOW - 5,
    }
    for path, value in overrides.items():
        target = summary
        keys = path.split(".")
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = value
    (directory / "summary.json").write_text(json.dumps(summary))
    (directory / "unsigned.b64").write_text(request["transaction_b64"] + "\n")
    (directory / "metis-request.json").write_text(json.dumps(request))
    return summary


class GateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.build, self.state, self.log = root / "build", root / "state", root / "ops.jsonl"
        self.build.mkdir()

    def gate(self, rpc=None, now=NOW):
        return sign_gate.run(self.build, rpc=rpc or FakeRpc(), now=lambda: now, state_dir=self.state, log_path=self.log)

    def assertDenied(self, reason, **kwargs):
        code, result = self.gate(**kwargs)
        self.assertEqual((code, result["decision"], result["reason"]), (2, "denied", reason))
        entry = oplog.read(self.log)[-1]
        self.assertEqual((entry["op"], entry["data"]["decision"], entry["data"]["reason"]), ("gate", "denied", reason))
        return result

    def test_pass_issues_short_lived_ticket_and_logs(self):
        summary = write_build(self.build)
        code, result = self.gate()
        self.assertEqual(code, 0, result)
        ticket = result["ticket"]
        self.assertEqual(ticket["message_sha256"], summary["metis_offline"]["report"]["message_sha256"])
        self.assertEqual(ticket["blocks_left"], 100)
        self.assertEqual(ticket["expires_at_epoch"], NOW + sign_gate.TICKET_TTL_S)
        self.assertTrue((self.state / "consumed" / ticket["message_sha256"]).exists())
        entry = oplog.read(self.log)[-1]
        self.assertEqual((entry["op"], entry["data"]["decision"]), ("gate", "approved"))
        self.assertNotIn("unsigned_path", entry["data"]["ticket"])

    def test_metis_fail(self):
        write_build(self.build, **{"metis_offline.exit": 2})
        self.assertDenied("metis_not_passed")
        write_build(self.build, **{"metis_offline.report.kind": "rejected"})
        self.assertDenied("metis_not_passed")

    def test_metis_fail_on_a_genuinely_rejected_build(self):
        case = fx.Case()
        case.tables = []
        write_build(self.build, case)
        self.assertDenied("metis_not_passed")

    def test_simulation_error(self):
        write_build(self.build, **{"simulation.err": {"InstructionError": [5, "Custom"]}})
        self.assertDenied("simulation_error")

    def test_simulation_output_below_min_or_persistent_account(self):
        write_build(self.build, **{"simulation.usdc_raw_delta": 10})
        self.assertDenied("simulation_output_below_min")
        write_build(self.build, **{"simulation.persistent_accounts_created": ["X"]})
        self.assertDenied("simulation_accounts")

    def test_sha_mismatch(self):
        write_build(self.build, **{"simulation.message_sha256": "00" * 32})
        self.assertDenied("message_sha_mismatch_simulation")
        write_build(self.build, **{"metis_offline.report.message_sha256": "11" * 32})
        self.assertDenied("message_sha_mismatch_check")

    def test_unsigned_file_swapped_after_check(self):
        write_build(self.build)
        other = fx.Case(instructions=fx.default_instructions(cu_limit=80_000))
        (self.build / "unsigned.b64").write_text(other.request()["transaction_b64"])
        self.assertDenied("request_wire_mismatch")

    def test_stale_age(self):
        write_build(self.build, **{"finished_at_epoch": NOW - 31})
        self.assertDenied("summary_too_old")
        write_build(self.build, **{"finished_at_epoch": None})
        self.assertDenied("summary_too_old")

    def test_low_block_margin(self):
        write_build(self.build)
        self.assertDenied("block_margin_low", rpc=FakeRpc(height=956))
        code, _ = self.gate(rpc=FakeRpc(height=955))
        self.assertEqual(code, 0)

    def test_expired_blockhash_fails_rerun_or_validity(self):
        write_build(self.build)
        self.assertDenied("blockhash_invalid", rpc=FakeRpc(valid=False))

    def test_ticket_reuse(self):
        write_build(self.build)
        self.assertEqual(self.gate()[0], 0)
        self.assertDenied("ticket_reused")

    def test_missing_files(self):
        self.assertDenied("build_files_missing")

    def test_gate_never_calls_send(self):
        write_build(self.build)
        rpc = FakeRpc()
        self.gate(rpc=rpc)
        self.assertEqual(set(rpc.calls), {"getBlockHeight", "isBlockhashValid"})


class OpLogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = Path(self.tmp.name) / "ops.jsonl"

    def test_append_only_jsonl(self):
        oplog.append("note", {"a": 1}, path=self.log)
        oplog.append("note", {"a": 2}, path=self.log, backfilled=True)
        lines = self.log.read_text().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual([json.loads(l)["data"]["a"] for l in lines], [1, 2])
        self.assertIs(json.loads(lines[1])["backfilled"], True)

    def test_refuses_raw_bytes_and_secrets(self):
        wire = fx.Case().request()["transaction_b64"]
        for data in ({"transaction_b64": "x"}, {"swapTransaction": "x"}, {"nested": {"data_b64": "x"}}, {"blob": wire},
                     {"list": [wire]}, {"b": b"\x00"}, {"private_key": "x"}, {"api_key": "x"}, {"seed": "x"}):
            with self.assertRaises(oplog.LogLeak, msg=str(list(data))):
                oplog.append("note", data, path=self.log)
        self.assertFalse(self.log.exists())

    def test_signature_pubkey_hash_allowed(self):
        sig = "57QRfRyxVsjfWhpvwfLXPteaoW9qunAGK1x9B1m991t6Rr8odyyRJDx8pCu8GcYQNWfe5ok3u7juPcWMeJa9svuS"
        oplog.append("sign", {"signature": sig, "signer": "8Xm6iFvk9J8rBi7BvRAbiZU2RAEE1b7AM714QmiEXQ8a", "h": "ab" * 32}, path=self.log)
        self.assertEqual(oplog.read(self.log)[0]["data"]["signature"], sig)

    def test_build_summary_logging_leaks_nothing(self):
        case = fx.Case()
        summary = {
            "quote": {"at": "t", "in": "1000000", "route": []},
            "build": {"at": "t", "blockhash": "b", "last_valid_block_height": 1},
            "metis_offline": {"at": "t", "exit": 0, "report": public_report(preflight(case.raw(), case.intent, case.evidence()))},
            "simulation": {"at": "t", "err": None, "logs_tail": ["x"]},
        }
        sha = oplog.log_build_summary(summary, path=self.log)
        text = self.log.read_text()
        self.assertEqual([e["op"] for e in oplog.read(self.log)], ["build", "check", "simulate"])
        self.assertTrue(all(e["message_sha256"] == sha for e in oplog.read(self.log)))
        self.assertNotIn(case.request()["transaction_b64"], text)
        self.assertNotIn(base64.b64encode(case.raw()[100:160]).decode(), text)

    def test_committed_log_has_no_blobs(self):
        path = Path(__file__).resolve().parents[1] / "logs" / "operations.jsonl"
        if not path.exists():
            self.skipTest("no committed log yet")
        for entry in oplog.read(path):
            oplog.scan(entry)
        self.assertIsNone(re.search(r"[A-Za-z0-9+/=]{101,}", path.read_text()))


class RecordResultTest(unittest.TestCase):
    def test_records_sign_and_result_with_deltas_and_ticket_binding(self):
        case = fx.Case()
        raw = case.raw()
        sha = hashlib.sha256(raw[65:]).hexdigest()
        user = b58encode(fx.USER)
        tx = {
            "slot": 7, "blockTime": 1_794_221_417,
            "transaction": [base64.b64encode(raw).decode(), "base64"],
            "meta": {"err": None, "fee": 5_000, "computeUnitsConsumed": 57_054,
                     "preBalances": [10_000_000] + [0] * 8, "postBalances": [8_995_000] + [0] * 8,
                     "loadedAddresses": {"writable": [], "readonly": []},
                     "preTokenBalances": [{"owner": user, "mint": "USDC", "uiTokenAmount": {"amount": "100"}}],
                     "postTokenBalances": [{"owner": user, "mint": "USDC", "uiTokenAmount": {"amount": "86010"}}]},
        }
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "ops.jsonl"
            ticket = {"ticket_id": "t1", "message_sha256": sha, "expires_at_epoch": 1_794_221_420}
            data = record_result.record("SIG", ticket=ticket, signer=user, rpc=FakeRpc(tx=tx), log_path=log, sleep=lambda s: None)
            entries = oplog.read(log)
            text = log.read_text()
        self.assertEqual([e["op"] for e in entries], ["sign", "result"])
        self.assertEqual(data["status"], "confirmed")
        self.assertEqual((data["fee_lamports"], data["compute_units"], data["signer_sol_delta_lamports"]), (5_000, 57_054, -1_005_000))
        self.assertEqual(data["signer_token_deltas_raw"], {"USDC": 85_910})
        self.assertIs(data["message_matches_ticket"], True)
        self.assertIs(data["landed_within_ticket_ttl"], True)
        self.assertEqual(entries[1]["message_sha256"], sha)
        self.assertNotIn(tx["transaction"][0], text)

    def test_not_found_is_logged(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "ops.jsonl"
            data = record_result.record("SIG", rpc=FakeRpc(tx=None), log_path=log, sleep=lambda s: None)
            self.assertEqual(data["status"], "not_found")
            self.assertEqual([e["op"] for e in oplog.read(log)], ["sign", "result"])


class ReadOnlyRpcTest(unittest.TestCase):
    def test_send_methods_are_refused(self):
        from tools import readonly_rpc

        for method in ("sendTransaction", "sendRawTransaction", "requestAirdrop"):
            self.assertNotIn(method, readonly_rpc.READ_ONLY_METHODS)
            with self.assertRaises(RuntimeError):
                readonly_rpc.rpc(method, [])

    def test_tools_never_sign_or_send(self):
        """No tool signs or sends, with TWO named exceptions: tools/turnkey_signer.py may request a
        Turnkey signature (disabled by default) but must never broadcast; tools/turnkey_broadcast.py is
        the only file that may name sendTransaction (needs config broadcast true AND --broadcast-confirm),
        and only turnkey_cli.py may import it."""
        root = Path(__file__).resolve().parents[1] / "tools"
        signer_exception = "turnkey_signer.py"
        broadcast_exception = "turnkey_broadcast.py"
        for path in root.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            if path.name not in (broadcast_exception, "turnkey_cli.py"):
                self.assertNotIn("turnkey_broadcast", text.replace("tools/turnkey_broadcast.py", ""), str(path))
            if path.name == broadcast_exception:
                self.assertEqual(text.count('"method": "sendTransaction"'), 1)
                continue
            needles = ["solana_send", "Keypair", "sign_message", "/execute"]
            if path.name != signer_exception:
                needles.append("signTransaction")
            for needle in needles:
                self.assertNotIn(needle, text, str(path))
            self.assertNotIn('"sendTransaction"', text.replace('rpc("sendTransaction"', ""), str(path))
        signer = (root / signer_exception).read_text(encoding="utf-8")
        for needle in ("sendTransaction", "sendRawTransaction", "broadcast_svm", "BROADCAST", "sol_send", "/submit/send", "readonly_rpc"):
            self.assertNotIn(needle, signer)
        self.assertEqual(re.findall(r"/public/v1/[a-z_/]+", signer), ["/public/v1/submit/sign_transaction"])


if __name__ == "__main__":
    unittest.main()
