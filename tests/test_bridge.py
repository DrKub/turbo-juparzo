"""Scanner -> turbo-juparzo bridge. Pipeline, clock and filesystem are fakes: no network, no signing."""

import datetime as dt
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from zoneinfo import ZoneInfo

from tools import bridge, jupiter_unsigned_build as build_mod, live_once, oplog
from tools.readonly_rpc import READ_ONLY_METHODS

KYIV = ZoneInfo("Europe/Kyiv")
NOW = 1_800_000_000.0
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t, KYIV).isoformat()


def real_buy(**kw) -> dict:
    """Shape copied from memedesk live/signals/0018_buy.json (scanner.accept)."""
    sig = {"id": "0018", "side": "buy", "mint": "483nprwoMSoGQgrojSHWiePMbKPre7ZRDy4f8YNSpump", "symbol": "HEREWEGO",
           "name": "Ah Shit, Here We Go Again", "pair": "67BwjNjSjbdPP3KPi6GDeevK25QGrprwFTTmQE4wSziR",
           "dexscreener_url": "https://dexscreener.com/solana/67bwjnjsjbdpp3kpi6gdeevk25qgrprwfttmqe4wszir",
           "price_usd": 0.001007, "size_sol": 0.0354, "mcap": 1003027.0, "liquidity": 81465.82, "volume_1h": 274686.59,
           "age_hours": 1.9149937723742592, "top10_pct": 9.829519870280304, "score": 72.887, "created_at": iso(NOW - 30)}
    sig.update(kw)
    return sig


def real_sell(**kw) -> dict:
    """Shape copied from memedesk live/signals/0009_sell.json (scanner.emit_sell)."""
    sig = {"id": "0009", "side": "sell", "mint": "DKtofLM6bhb54GQy5YFSWQjQmGiZqAkjzkQJ1LTopump", "symbol": "HODL",
           "reason": "stop_loss", "trigger_price": 0.002802, "token_amount": 956.938439, "created_at": iso(NOW - 30)}
    sig.update(kw)
    return sig


def supported(**kw) -> dict:
    """The only intent the existing build can produce: 0.001 SOL -> USDC."""
    base = {"id": "0100", "mint": USDC, "symbol": "USDC", "size_sol": 0.001}
    base.update(kw)
    return real_buy(**base)


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t


class FakePipeline:
    def __init__(self, result=None, exc=None, clock=None, advance=0.0, on_call=None):
        self.calls, self.result, self.exc, self.clock, self.advance, self.on_call = [], result, exc, clock, advance, on_call

    def __call__(self, out_dir, intent):
        self.calls.append((out_dir, intent))
        if self.clock:
            self.clock.t += self.advance
        if self.on_call:
            self.on_call()
        if self.exc:
            raise self.exc
        return self.result or approved()


def approved(expires=NOW + 20):
    return 0, {"decision": "approved", "reason": "ok", "unsigned_b64_path": "/tmp/x/unsigned.b64", "ticket_path": "/tmp/t.json",
               "ticket": {"ticket_id": "abcd", "message_sha256": "0" * 64, "expires_at": iso(expires), "expires_at_epoch": expires,
                          "blocks_left": 80}}


class BridgeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.log = self.dir / "ops.jsonl"
        self.fills = self.dir / "fills"
        self.fills.mkdir()
        self.clock = Clock()

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, pipeline=None, **kw):
        self.pipeline = pipeline or FakePipeline()
        return bridge.Bridge(state_dir=self.dir / "state", log_path=self.log, pipeline=self.pipeline, clock=self.clock,
                             fills_dir=self.fills, out_root=self.dir / "out", **kw)

    def events(self):
        return [e["data"]["event"] for e in oplog.read(self.log)]

    def claim(self, key):
        return json.loads((self.dir / "state" / "claims" / f"{key}.json").read_text())

    # --- real memedesk signals -------------------------------------------------
    def test_real_buy_signal_stops_unsupported_pair_without_pipeline(self):
        b = self.make()
        code, rec = b.process(real_buy())
        self.assertEqual((code, rec["reason"], rec["retry"]), (2, "unsupported_pair", False))
        self.assertEqual(self.pipeline.calls, [])
        self.assertEqual(self.claim(rec["signal_key"])["status"], "stopped")
        self.assertEqual(json.loads((self.fills / "0018_buy_fill.json").read_text()), {"status": "failed", "reason": "bridge:unsupported_pair"})
        self.assertEqual(self.events(), ["received", "mapped", "stop"])
        self.assertEqual(len(list((self.dir / "state" / "notifications").iterdir())), 1)
        self.assertFalse(b.kill_file.exists())

    def test_real_sell_signal_maps_token_to_sol_and_stops(self):
        code, rec = self.make().process(real_sell())
        self.assertEqual((code, rec["reason"]), (2, "unsupported_pair"))
        mapped = [e for e in oplog.read(self.log) if e["data"]["event"] == "mapped"][0]["data"]["intent"]
        self.assertEqual((mapped["input_mint"], mapped["output_mint"], mapped["amount_unit"]),
                         (real_sell()["mint"], build_mod.INPUT_MINT, "token_ui"))
        self.assertTrue((self.fills / "0009_sell_fill.json").exists())

    def test_buy_intent_is_sol_to_mint_in_lamports(self):
        intent = bridge.map_intent(bridge.parse_signal(real_buy(), NOW))
        self.assertEqual((intent["input_mint"], intent["output_mint"], intent["amount_raw"]),
                         (build_mod.INPUT_MINT, real_buy()["mint"], 35_400_000))

    # --- single use ------------------------------------------------------------
    def test_identical_signal_is_refused_and_pipeline_runs_once(self):
        b = self.make()
        self.assertEqual(b.process(supported())[0], 0)
        code, rec = b.process(supported())
        self.assertEqual((code, rec["reason"]), (4, "duplicate_signal"))
        self.assertEqual(len(self.pipeline.calls), 1)

    def test_stopped_signal_is_not_retried(self):
        b = self.make(FakePipeline(result=(2, {"decision": "denied", "reason": "block_margin_low"})))
        self.assertEqual(b.process(supported())[0], 2)
        self.assertEqual(b.process(supported())[0], 4)
        self.assertEqual(len(self.pipeline.calls), 1)

    def test_new_signal_for_same_coin_is_allowed(self):
        b = self.make()
        self.assertEqual(b.process(supported())[0], 0)
        self.assertEqual(b.process(supported(id="0101", created_at=iso(NOW - 3)))[0], 0)
        self.assertEqual(len(self.pipeline.calls), 2)

    # --- supported path --------------------------------------------------------
    def test_supported_signal_reaches_ready_to_sign_handoff(self):
        b = self.make()
        code, rec = b.process(supported())
        self.assertEqual((code, rec["status"], rec["ticket_id"]), (0, "ready_to_sign", "abcd"))
        handoff = json.loads(Path(rec["handoff"]).read_text())
        self.assertEqual(handoff["signer"], build_mod.USER)
        self.assertNotIn("transaction_b64", json.dumps(handoff))
        self.assertEqual(self.claim(rec["signal_key"])["status"], "ready_to_sign")
        _, intent = self.pipeline.calls[0]
        self.assertEqual((intent["output_mint"], intent["amount_raw"]), (USDC, build_mod.AMOUNT))
        self.assertEqual(self.events(), ["received", "mapped", "pipeline_start", "ready_to_sign"])
        self.assertEqual(list(self.fills.iterdir()), [])

    def test_amount_above_preflight_cap_is_refused(self):
        code, rec = self.make().process(supported(size_sol=0.002))
        self.assertEqual((code, rec["reason"]), (2, "amount_over_limit"))
        self.assertEqual(self.pipeline.calls, [])

    def test_amount_the_build_cannot_produce_is_refused(self):
        code, rec = self.make().process(supported(size_sol=0.0005))
        self.assertEqual((code, rec["reason"]), (2, "pipeline_intent_mismatch"))
        self.assertEqual(self.pipeline.calls, [])

    # --- stop rule -------------------------------------------------------------
    def test_gate_or_check_denial_stops_signal_without_halting(self):
        for n, reason in enumerate(("simulation_error", "metis_not_passed", "build_failed")):
            with self.subTest(reason=reason):
                b = self.make(FakePipeline(result=(2, {"decision": "denied", "reason": reason})))
                code, rec = b.process(supported(id=str(200 + n), created_at=iso(NOW - n)))
                self.assertEqual((code, rec["reason"]), (2, f"pipeline_denied:{reason}"))
                self.assertFalse(b.kill_file.exists())

    def test_network_loss_halts_and_trips_kill_switch(self):
        for n, exc in enumerate((urllib.error.URLError("down"), TimeoutError(), ConnectionResetError())):
            with self.subTest(exc=type(exc).__name__):
                b = self.make(FakePipeline(exc=exc))
                if b.kill_file.exists():
                    b.kill_file.unlink()
                code, rec = b.process(supported(id="0300", created_at=iso(NOW - 1), score=n))
                self.assertEqual((code, rec["reason"], rec["kill_switch_tripped"]), (3, "network_loss", True))
                self.assertTrue(b.kill_file.exists())
                self.assertEqual(self.claim(rec["signal_key"])["status"], "halted")

    def test_rpc_error_and_unexpected_exception_halt(self):
        for exc, reason in ((RuntimeError("getBlockHeight: x"), "rpc_error"), (KeyError("swapTransaction"), "pipeline_exception")):
            with self.subTest(reason=reason):
                b = self.make(FakePipeline(exc=exc))
                if b.kill_file.exists():
                    b.kill_file.unlink()
                code, rec = b.process(supported(id="0400", created_at=iso(NOW - 2), score=len(reason)))
                self.assertEqual((code, rec["reason"]), (3, reason))
                self.assertNotIn("getBlockHeight", json.dumps(oplog.read(self.log)))

    def test_kill_switch_blocks_without_claim_or_pipeline(self):
        b = self.make(FakePipeline(exc=TimeoutError()))
        b.process(supported())
        self.pipeline.exc = None
        code, rec = b.process(supported(id="0500", created_at=iso(NOW - 4)))
        self.assertEqual((code, rec["reason"]), (3, "kill_switch"))
        self.assertEqual(len(self.pipeline.calls), 1)
        self.assertFalse((self.dir / "state" / "claims" / f"{rec['signal_key']}.json").exists())
        b.kill_file.unlink()  # a human clears it; the new signal then runs
        self.assertEqual(b.process(supported(id="0500", created_at=iso(NOW - 4)))[0], 0)

    def test_timeout_budget_halts_and_discards_ticket(self):
        b = self.make(FakePipeline(clock=self.clock, advance=120), budget_s=90)
        code, rec = b.process(supported())
        self.assertEqual((code, rec["reason"]), (3, "timeout"))
        self.assertFalse((self.dir / "state" / "ready").exists())

    def test_expired_ticket_is_not_handed_off(self):
        b = self.make(FakePipeline(result=approved(expires=NOW - 1)))
        code, rec = b.process(supported())
        self.assertEqual((code, rec["reason"]), (2, "ticket_expired"))
        self.assertFalse((self.dir / "state" / "ready").exists())

    def test_kill_switch_during_run_stops_before_handoff(self):
        holder = {}
        b = self.make(FakePipeline(on_call=lambda: holder["b"].kill_file.write_text("manual")))
        holder["b"] = b
        b.kill_file.parent.mkdir(parents=True, exist_ok=True)
        code, rec = b.process(supported())
        self.assertEqual((code, rec["reason"]), (2, "kill_switch_during_run"))
        self.assertFalse((self.dir / "state" / "ready").exists())

    def test_existing_fill_is_never_overwritten(self):
        (self.fills / "0018_buy_fill.json").write_text('{"status": "filled"}')
        code, rec = self.make().process(real_buy())
        self.assertEqual(code, 2)
        self.assertNotIn("failed_fill_written", rec)
        self.assertEqual(json.loads((self.fills / "0018_buy_fill.json").read_text()), {"status": "filled"})

    # --- validation ------------------------------------------------------------
    def test_invalid_signals_are_consumed_and_stopped(self):
        cases = {
            "side": real_buy(side="close"), "id": real_buy(id="../x"), "mint": real_buy(mint="Mint111"),
            "created_at": real_buy(created_at="2026-11-08T05:38:35"), "size_sol": real_buy(size_sol=float("nan")),
        }
        for field, sig in cases.items():
            with self.subTest(field=field):
                code, rec = self.make().process(sig)
                self.assertEqual((code, rec["reason"], rec.get("field")), (2, "invalid_signal", field))
        for bad in (True, -1, 0, "0.03", None):
            with self.subTest(size=bad):
                self.assertEqual(self.make().process(real_buy(size_sol=bad, score=str(bad)))[1]["reason"], "invalid_signal")

    def test_stale_and_future_signals(self):
        self.assertEqual(self.make().process(real_buy(created_at=iso(NOW - 601)))[1]["reason"], "signal_expired")
        self.assertEqual(self.make().process(real_buy(created_at=iso(NOW + 60)))[1]["reason"], "signal_from_future")
        self.assertEqual(self.make(max_age_s=60).process(real_buy(created_at=iso(NOW - 61)))[1]["reason"], "signal_expired")
        with self.assertRaises(ValueError):
            self.make(max_age_s=601)

    def test_non_object_signal_is_stopped_unclaimed(self):
        code, rec = self.make().process(None)
        self.assertEqual((code, rec["reason"], rec["signal_key"]), (2, "invalid_signal", None))

    # --- safety surface --------------------------------------------------------
    def test_no_send_capability(self):
        self.assertNotIn("sendTransaction", READ_ONLY_METHODS)
        source = Path(bridge.__file__).read_text()
        code_only = source.split('"""', 2)[2]
        for word in ("sendTransaction", "sendRawTransaction", "urlopen", "keypair", "sign_message"):
            self.assertNotIn(word, code_only)

    def test_default_pipeline_refuses_unsupported_intent_before_network(self):
        intent = bridge.map_intent(bridge.parse_signal(real_buy(), NOW))
        with self.assertRaises(bridge.Stop) as ctx:
            bridge.default_pipeline(self.dir / "o", intent)
        self.assertEqual(ctx.exception.reason, "unsupported_pair")

    # --- CLI ---------------------------------------------------------------------
    def test_cli_stdin_and_file(self):
        sig = real_buy(created_at=iso(__import__("time").time() - 5))
        args = ["--state-dir", str(self.dir / "cli"), "--log-path", str(self.log), "--out", str(self.dir / "o")]
        out = io.StringIO()
        with unittest.mock.patch("sys.stdout", out):
            self.assertEqual(bridge.main(args + ["--stdin"], stdin=io.StringIO(json.dumps(sig))), 2)
        self.assertEqual(json.loads(out.getvalue())["reason"], "unsupported_pair")
        path = self.dir / "s.json"
        path.write_text(json.dumps(sig))
        with unittest.mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(bridge.main(args + ["--signal", str(path)]), 4)
        with unittest.mock.patch("sys.stdout", io.StringIO()), unittest.mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(bridge.main(["--help"]), 0)
            self.assertEqual(bridge.main([]), 1)
            self.assertEqual(bridge.main(args), 1)
            self.assertEqual(bridge.main(args + ["--stdin", "--signal", str(path)]), 1)


class LiveOnceRunOnceTest(unittest.TestCase):
    def test_run_once_uses_injected_build_and_gate(self):
        out = Path("/nonexistent-dir")
        code, res = live_once.run_once(out, build=lambda argv: 0, gate=lambda d: (2, {"decision": "denied", "reason": "x", "k": 1}))
        self.assertEqual((code, res), (2, {"decision": "denied", "reason": "x", "k": 1}))
        code, res = live_once.run_once(out, build=lambda argv: 1, gate=None)
        self.assertEqual((code, res["reason"]), (2, "build_failed"))
        ticket = {"ticket_id": "t1"}
        code, res = live_once.run_once(out, build=lambda argv: 0, gate=lambda d: (0, {"decision": "approved", "reason": "ok", "ticket": ticket}))
        self.assertEqual((code, res["ticket"], res["unsigned_b64_path"]), (0, ticket, "/nonexistent-dir/unsigned.b64"))


import unittest.mock  # noqa: E402

if __name__ == "__main__":
    unittest.main()
