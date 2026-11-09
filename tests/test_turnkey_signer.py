"""Turnkey signer: fakes only. No Turnkey account, no network, no broadcast."""

import base64
import hashlib
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from metis_offline import fixtures as fx
from metis_offline.base58 import b58encode
from metis_offline.wire import parse_transaction, serialize
from metis_offline.pda import associated_token_address
from metis_offline.preflight import ATA, TOKEN, USDC, WSOL
from tools import bridge, oplog, turnkey_signer as ts, wallet

NOW = 1_800_000_000.0
# Documented vector: docs.turnkey.com/developer-reference/api-overview/stamps (`turnkey request --no-post`).
DOC_BODY = '{"payload": "hello from TKHQ"}'
DOC_STAMP = ("eyJwdWJsaWNLZXkiOiIwMzI3YTUwMDMyZTZmMDYzMWQ1NjA1YjZhZGEzMmI3NzkwNzRmMzQ2ZTgxYjY4ZTEyODAxNjQwZjFjOWVlMDNkYWUiLCJzaWduYXR1cmUiOiIzMDQ0MDIy"
             "MDM2MjNkZWZkNjE4ZWIzZTIxOTk3MDQ5NjQwN2ViZTkyNDQ3MzE3ZGFkNzVlNDEyYmQ0YTYyNjdjM2I1ZTIyMjMwMjIwMjQ1Yjc0MDg0OGE3MmQwOGI2MGQ2Yzg0ZjMzOTcz"
             "N2I2M2RiM2JjYmFkYjNiZDBkY2IxYmZiODY1NzE1ZDhiNSIsInNjaGVtZSI6IlNJR05BVFVSRV9TQ0hFTUVfVEtfQVBJX1AyNTYifQ")

WALLET = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
WALLET_PUB = WALLET.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
API = ec.derive_private_key(0x1234567890ABCDEF1234567890ABCDEF1234567890ABCDEF1234567890ABCDEF, ec.SECP256R1())
API_PRIV = format(API.private_numbers().private_value, "064x")
API_PUB = API.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.CompressedPoint).hex()


def config(**kw):
    cfg = {"enabled": True, "broadcast": False, "transport": "none", "api_base": "https://api.turnkey.com",
           "per_trade_max_lamports": 1_000_000, "daily_max_lamports": 2_500_000, "request_timeout_s": 10}
    cfg.update(kw)
    return cfg


def creds(sign_with=None):
    return ts.Credentials(API_PUB, API_PRIV, "org-123", sign_with or b58encode(WALLET_PUB))


def case_for(payer: bytes, lamports: int = fx.AMOUNT) -> fx.Case:
    case = fx.Case(static=[payer] + list(fx.STATIC[1:]))
    if lamports != fx.AMOUNT:
        case.instructions = [ix if not (ix.program_index == fx.I_SYSTEM) else fx.transfer_ix(lamports) for ix in case.instructions]
    return case


class FakeTransport:
    """Signs the message locally with the test ed25519 key, the way Turnkey would return it."""

    def __init__(self, status=200, activity_status="ACTIVITY_STATUS_COMPLETED", exc=None, tamper=None, key=WALLET):
        self.calls, self.status, self.activity_status, self.exc, self.tamper, self.key = [], status, activity_status, exc, tamper, key

    def __call__(self, url, body, headers, timeout):
        self.calls.append((url, body, headers, timeout))
        if self.exc:
            raise self.exc
        tx = parse_transaction(bytes.fromhex(json.loads(body)["parameters"]["unsignedTransaction"]))
        signed = tx.__class__(*((self.key.sign(tx.message),),) + tuple(getattr(tx, f) for f in list(tx.__dataclass_fields__)[1:]))
        raw = serialize(signed)
        if self.tamper:
            raw = self.tamper(raw)
        return self.status, {"activity": {"id": "act-1", "status": self.activity_status,
                                          "result": {"signTransactionResult": {"signedTransaction": raw.hex()}}}}


class SignerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.log = self.dir / "ops.jsonl"
        self.gate = self.dir / "gate"
        self.now = [NOW]

    def tearDown(self):
        self.tmp.cleanup()

    def clock(self):
        return self.now[0]

    def wallet_file(self) -> Path:
        """A wallet.json whose 'turnkey' profile is the test key (the real file pins 9Ao3, whose key we do not hold)."""
        path = self.dir / "wallet.json"
        if not path.exists():
            addr = b58encode(WALLET_PUB)
            ata = lambda mint: b58encode(associated_token_address(WALLET_PUB, mint, TOKEN, ATA))  # noqa: E731
            path.write_text(json.dumps({"default_profile": "turnkey", "profiles": {"turnkey": {
                "address": addr, "signer": "turnkey", "wsol_ata": ata(WSOL), "usdc_ata": ata(USDC)}}}))
        return path

    def handoff(self, payer=WALLET_PUB, lamports=fx.AMOUNT, ticket_id="t1", expires=NOW + 20, write_gate=True):
        raw = case_for(payer, lamports).raw()
        wire = base64.b64encode(raw).decode()
        build = self.dir / f"build-{ticket_id}"
        build.mkdir(exist_ok=True)
        (build / "unsigned.b64").write_text(wire + "\n")
        sha = hashlib.sha256(parse_transaction(raw).message).hexdigest()
        ticket = {"ticket_id": ticket_id, "message_sha256": sha, "unsigned_sha256": hashlib.sha256(wire.encode()).hexdigest(),
                  "blockhash": b58encode(fx.BLOCKHASH), "last_valid_block_height": 1000, "expires_at_epoch": expires, "expires_at": "x"}
        if write_gate:
            (self.gate / "consumed").mkdir(parents=True, exist_ok=True)
            (self.gate / "consumed" / sha).write_text(json.dumps(ticket))
        return {"status": "ready_to_sign", "ticket": dict(ticket), "unsigned_b64_path": str(build / "unsigned.b64"),
                "wallet_profile": "turnkey", "signer": b58encode(WALLET_PUB)}

    def signer(self, transport=None, cfg=None, **kw):
        self.transport = transport or FakeTransport()
        return ts.TurnkeySigner(cfg or config(), kw.pop("creds", None) or creds(), self.transport, clock=self.clock,
                                state_dir=self.dir / "tk", kill_file=self.dir / "KILL", gate_state_dir=self.gate, log_path=self.log,
                                wallet_path=kw.pop("wallet_path", None) or self.wallet_file(), **kw)

    def stop_reason(self, signer, handoff):
        with self.assertRaises(bridge.Stop) as ctx:
            signer(handoff)
        return ctx.exception

    # --- stamping ------------------------------------------------------------
    def test_documented_stamp_vector_verifies(self):
        self.assertTrue(ts.verify_stamp(DOC_BODY, DOC_STAMP))
        self.assertFalse(ts.verify_stamp(DOC_BODY + " ", DOC_STAMP))

    def test_own_stamp_format_and_signature(self):
        stamp = ts.p256_stamp(DOC_BODY, API_PUB, API_PRIV)
        self.assertNotIn("=", stamp)
        decoded = json.loads(base64.urlsafe_b64decode(stamp + "=" * (-len(stamp) % 4)))
        self.assertEqual(set(decoded), {"publicKey", "scheme", "signature"})
        self.assertEqual((decoded["publicKey"], decoded["scheme"]), (API_PUB, "SIGNATURE_SCHEME_TK_API_P256"))
        self.assertTrue(decoded["signature"].startswith("30"))  # DER
        self.assertTrue(ts.verify_stamp(DOC_BODY, stamp))
        with self.assertRaises(ts.StamperUnavailable):
            ts.p256_stamp(DOC_BODY, "02" + "00" * 32, API_PRIV)

    def test_request_body_shape(self):
        body = json.loads(ts.sign_request_body(creds(), b"\x01\x02", 1700000000000))
        self.assertEqual(body, {"type": "ACTIVITY_TYPE_SIGN_TRANSACTION_V2", "timestampMs": "1700000000000", "organizationId": "org-123",
                                "parameters": {"signWith": b58encode(WALLET_PUB), "unsignedTransaction": "0102", "type": "TRANSACTION_TYPE_SOLANA"}})

    # --- happy path (fake Turnkey) ------------------------------------------
    def test_signs_once_and_never_broadcasts(self):
        s = self.signer()
        out = s(self.handoff())
        self.assertEqual(out["status"], "signed_not_broadcast")
        url, body, headers, _ = self.transport.calls[0]
        self.assertEqual(url, "https://api.turnkey.com/public/v1/submit/sign_transaction")
        self.assertTrue(ts.verify_stamp(body, headers["X-Stamp"]))
        self.assertEqual(len(self.transport.calls), 1)
        sign = [e for e in oplog.read(self.log) if e["op"] == "sign"][0]
        self.assertEqual((sign["data"]["broadcast"], sign["data"]["signature"]), (False, out["signature"]))
        self.assertEqual(Path(out["signed_path"]).stat().st_mode & 0o777, 0o600)
        self.assertNotIn(body, Path(self.log).read_text())

    # --- ticket --------------------------------------------------------------
    def test_ticket_required_expired_reused_and_forged(self):
        s = self.signer()
        self.assertEqual(self.stop_reason(s, {"ticket": {}, "wallet_profile": "turnkey", "signer": b58encode(WALLET_PUB)}).reason, "ticket_missing")
        self.assertEqual(self.stop_reason(s, self.handoff(ticket_id="nogate", write_gate=False)).reason, "ticket_not_issued_by_gate")
        h = self.handoff(ticket_id="exp", expires=NOW - 1)
        self.assertEqual(self.stop_reason(s, h).reason, "ticket_expired")
        h = self.handoff(ticket_id="forged")
        h["ticket"]["expires_at_epoch"] = NOW + 3600
        self.assertEqual(self.stop_reason(s, h).reason, "ticket_mismatch")
        h = self.handoff(ticket_id="once")
        s(h)
        self.assertEqual(self.stop_reason(s, h).reason, "ticket_reused")
        self.assertEqual(len(self.transport.calls), 1)

    def test_hash_mismatches(self):
        s = self.signer()
        h = self.handoff(ticket_id="a")
        Path(h["unsigned_b64_path"]).write_text(base64.b64encode(case_for(WALLET_PUB, 999_999).raw()).decode())
        self.assertEqual(self.stop_reason(s, h).reason, "unsigned_mismatch")
        h = self.handoff(ticket_id="b")
        other = base64.b64encode(case_for(WALLET_PUB, 999_998).raw()).decode()
        Path(h["unsigned_b64_path"]).write_text(other)
        gate_ticket = json.loads((self.gate / "consumed" / h["ticket"]["message_sha256"]).read_text())
        gate_ticket["unsigned_sha256"] = h["ticket"]["unsigned_sha256"] = hashlib.sha256(other.encode()).hexdigest()
        (self.gate / "consumed" / h["ticket"]["message_sha256"]).write_text(json.dumps(gate_ticket))
        self.assertEqual(self.stop_reason(s, h).reason, "message_hash_mismatch")
        self.assertEqual(self.transport.calls, [])

    def test_fee_payer_must_be_turnkey_wallet(self):
        stop = self.stop_reason(self.signer(), self.handoff(payer=fx.USER))
        self.assertEqual(stop.reason, "fee_payer_mismatch")

    # --- caps / kill switch / config ----------------------------------------
    def test_per_trade_and_daily_caps(self):
        s = self.signer(cfg=config(per_trade_max_lamports=500_000))
        self.assertEqual(self.stop_reason(s, self.handoff(ticket_id="big")).reason, "per_trade_cap")
        s = self.signer()
        s(self.handoff(ticket_id="d1"))
        s(self.handoff(ticket_id="d2", lamports=999_999))
        stop = self.stop_reason(s, self.handoff(ticket_id="d3", lamports=999_998))
        self.assertEqual((stop.reason, stop.facts["spent_today"]), ("daily_cap", 2_000_000))  # route_v2 in_amount (1_000_000) dominates the smaller transfers
        self.assertEqual(len(self.transport.calls), 2)
        self.now[0] += 86_400  # next Kyiv day
        s(self.handoff(ticket_id="d4", lamports=999_997, expires=self.now[0] + 20))

    def test_failed_request_still_counts_against_daily_cap(self):
        s = self.signer(FakeTransport(status=403), cfg=config(daily_max_lamports=1_000_000))
        self.stop_reason(s, self.handoff(ticket_id="x1"))
        self.assertEqual(self.stop_reason(s, self.handoff(ticket_id="x2", lamports=10)).reason, "daily_cap")

    def test_kill_switch_and_disabled_config(self):
        (self.dir / "KILL").write_text("x")
        stop = self.stop_reason(self.signer(), self.handoff())
        self.assertEqual((stop.reason, stop.halt), ("kill_switch", True))
        (self.dir / "KILL").unlink()
        self.assertEqual(self.stop_reason(self.signer(cfg=config(enabled=False)), self.handoff(ticket_id="z")).reason, "signer_disabled")
        self.assertEqual(self.transport.calls, [])

    def test_config_and_env_loading_fail_closed(self):
        path = self.dir / "c.json"
        for cfg, err in ((config(broadcast=True), "broadcast_not_implemented"), (config(per_trade_max_lamports=2_000_000), "per_trade_above_preflight_cap"),
                         (config(daily_max_lamports=10), "daily_below_per_trade")):
            path.write_text(json.dumps(cfg))
            with self.assertRaisesRegex(ts.ConfigError, err):
                ts.load_config(path)
        repo_cfg = ts.load_config(ts.CONFIG_PATH)
        self.assertFalse(repo_cfg["enabled"])
        with self.assertRaisesRegex(ts.ConfigError, "signer_disabled"):
            ts.TurnkeySigner.from_environment(ts.CONFIG_PATH, env={})
        path.write_text(json.dumps(config()))
        with self.assertRaisesRegex(ts.ConfigError, "missing_env"):
            ts.TurnkeySigner.from_environment(path, env={"TURNKEY_ORG_ID": "o"})
        env = {"TURNKEY_API_PUBLIC_KEY": API_PUB, "TURNKEY_API_PRIVATE_KEY": API_PRIV, "TURNKEY_ORG_ID": "o", "TURNKEY_SIGN_WITH": b58encode(WALLET_PUB)}
        with self.assertRaisesRegex(ts.ConfigError, "transport_not_configured"):
            ts.TurnkeySigner.from_environment(path, env=env)
        self.assertNotIn(API_PRIV, repr(ts.Credentials.from_env(env)))

    # --- Turnkey answers ----------------------------------------------------
    def test_policy_denial_and_http_errors_stop(self):
        cases = [(FakeTransport(activity_status="ACTIVITY_STATUS_FAILED"), "turnkey_status:ACTIVITY_STATUS_FAILED", False),
                 (FakeTransport(activity_status="ACTIVITY_STATUS_CONSENSUS_NEEDED"), "turnkey_status:ACTIVITY_STATUS_CONSENSUS_NEEDED", False),
                 (FakeTransport(status=403), "turnkey_http_403", False), (FakeTransport(status=401), "turnkey_http_401", False),
                 (FakeTransport(status=429), "turnkey_http_429", True), (FakeTransport(status=503), "turnkey_http_503", True)]
        for n, (transport, reason, halt) in enumerate(cases):
            with self.subTest(reason=reason):
                stop = self.stop_reason(self.signer(transport, cfg=config(daily_max_lamports=10_000_000)), self.handoff(ticket_id=f"h{n}"))
                self.assertEqual((stop.reason, stop.halt), (reason, halt))

    def test_network_loss_and_timeout_halt(self):
        for n, exc in enumerate((urllib.error.URLError("down"), TimeoutError(), ConnectionResetError(), ValueError("x"))):
            with self.subTest(exc=type(exc).__name__):
                stop = self.stop_reason(self.signer(FakeTransport(exc=exc), cfg=config(daily_max_lamports=10_000_000)), self.handoff(ticket_id=f"n{n}"))
                self.assertTrue(stop.halt)
                self.assertIn(stop.reason, ("turnkey_network_loss", "turnkey_transport_exception"))

    def test_signed_result_is_verified(self):
        other = Ed25519PrivateKey.from_private_bytes(bytes([7]) * 32)
        cfg = config(daily_max_lamports=10_000_000)
        stop = self.stop_reason(self.signer(FakeTransport(key=other), cfg=cfg), self.handoff(ticket_id="s1"))
        self.assertEqual(stop.reason, "signed_signature_invalid")

        def flip_last_byte(raw):
            return raw[:-1] + bytes([raw[-1] ^ 1])
        stop = self.stop_reason(self.signer(FakeTransport(tamper=flip_last_byte), cfg=cfg), self.handoff(ticket_id="s2"))
        self.assertIn(stop.reason, ("signed_message_mismatch", "signed_unparseable"))

    def test_requires_turnkey_profile_handoff(self):
        s = self.signer()
        h = self.handoff()
        h["wallet_profile"] = "phantom"
        self.assertEqual(self.stop_reason(s, h).reason, "wallet_profile_not_turnkey")
        h = self.handoff(ticket_id="t2")
        h["signer"] = "8Xm6iFvk9J8rBi7BvRAbiZU2RAEE1b7AM714QmiEXQ8a"
        self.assertEqual(self.stop_reason(s, h).reason, "wallet_profile_not_turnkey")
        self.assertEqual(self.transport.calls, [])

    def test_sign_with_must_equal_turnkey_profile(self):
        with self.assertRaisesRegex(ts.ConfigError, "sign_with_not_turnkey_profile"):
            self.signer(creds=creds(sign_with="8Xm6iFvk9J8rBi7BvRAbiZU2RAEE1b7AM714QmiEXQ8a"))
        with self.assertRaisesRegex(ts.ConfigError, "sign_with_not_turnkey_profile"):  # real config pins 9Ao3
            self.signer(wallet_path=wallet.CONFIG_PATH)
        repo = ts.TurnkeySigner(config(), creds(sign_with="9Ao3ggbuUaiWPzYKr4VdsNQ9LMzM6MPmraZRVv4gv1UW"), FakeTransport(),
                                state_dir=self.dir / "tk2", kill_file=self.dir / "KILL", gate_state_dir=self.gate)
        self.assertEqual(repo.profile.address, "9Ao3ggbuUaiWPzYKr4VdsNQ9LMzM6MPmraZRVv4gv1UW")

    def test_expiry_rechecked_after_stamping(self):
        def slow_stamper(body, pub, priv):
            self.now[0] += 30
            return ts.p256_stamp(body, pub, priv)
        stop = self.stop_reason(self.signer(stamper=slow_stamper), self.handoff())
        self.assertEqual(stop.reason, "ticket_expired")
        self.assertEqual(self.transport.calls, [])


class BridgeSignerStageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_bridge(self, signer):
        def pipeline(out_dir, intent):
            return 0, {"decision": "approved", "reason": "ok", "unsigned_b64_path": "/x", "ticket_path": "/t",
                       "ticket": {"ticket_id": "abc", "message_sha256": "0" * 64, "expires_at": "x", "expires_at_epoch": NOW + 20}}
        b = bridge.Bridge(state_dir=self.dir / "s", log_path=self.dir / "l", pipeline=pipeline, clock=lambda: NOW,
                          out_root=self.dir / "o", signer=signer)
        import datetime as dt
        sig = {"id": "0100", "side": "buy", "mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "symbol": "USDC", "size_sol": 0.001,
               "created_at": dt.datetime.fromtimestamp(NOW - 5, oplog.KYIV).isoformat()}
        return b, b.process(sig)

    def test_signer_stage_success_and_stop_rule(self):
        _, (code, rec) = self.run_bridge(lambda h: {"signature": "S", "signed_path": "/p"})
        self.assertEqual((code, rec["status"]), (0, "signed_not_broadcast"))

        def deny(h):
            raise bridge.Stop("turnkey_status:ACTIVITY_STATUS_FAILED")
        self.tmp.cleanup(); self.tmp = tempfile.TemporaryDirectory(); self.dir = Path(self.tmp.name)
        b, (code, rec) = self.run_bridge(deny)
        self.assertEqual((code, rec["reason"]), (2, "turnkey_status:ACTIVITY_STATUS_FAILED"))
        self.assertFalse(b.kill_file.exists())

        def down(h):
            raise bridge.Stop("turnkey_network_loss", halt=True)
        self.tmp.cleanup(); self.tmp = tempfile.TemporaryDirectory(); self.dir = Path(self.tmp.name)
        b, (code, rec) = self.run_bridge(down)
        self.assertEqual((code, rec["reason"]), (3, "turnkey_network_loss"))
        self.assertTrue(b.kill_file.exists())

        def boom(h):
            raise KeyError("x")
        self.tmp.cleanup(); self.tmp = tempfile.TemporaryDirectory(); self.dir = Path(self.tmp.name)
        b, (code, rec) = self.run_bridge(boom)
        self.assertEqual((code, rec["reason"]), (3, "signer_exception"))

    def test_bridge_cli_refuses_turnkey_when_disabled(self):
        import io
        from unittest import mock
        with mock.patch("sys.stderr", io.StringIO()) as err, mock.patch("sys.stdout", io.StringIO()):
            code = bridge.main(["--stdin", "--turnkey-sign", "--state-dir", str(self.dir / "s"), "--log-path", str(self.dir / "l")],
                               stdin=io.StringIO("{}"))
        self.assertEqual(code, 1)
        self.assertIn("signer_disabled", err.getvalue())

    def test_policy_file_is_well_formed(self):
        doc = json.loads((Path(ts.ROOT) / "config" / "turnkey" / "policies.json").read_text())
        names = set()
        for policy in doc["policies"]:
            self.assertEqual(set(policy) - {"consensus"}, {"policyName", "notes", "effect", "condition"})
            self.assertIn(policy["effect"], ("EFFECT_ALLOW", "EFFECT_DENY"))
            names.add(policy["policyName"])
            if policy["effect"] == "EFFECT_ALLOW":
                self.assertIn("<API_USER_ID>", policy["consensus"])
                self.assertIn("activity.type == 'ACTIVITY_TYPE_SIGN_TRANSACTION_V2'", policy["condition"])
            self.assertEqual(policy["condition"].count("("), policy["condition"].count(")"))
            self.assertNotIn('"', policy["condition"])
        self.assertEqual(len(names), len(doc["policies"]))
        self.assertIn("3YxVKCTSZUvQGFPL51dzXPoFj82k4v4NJC6cSxDuyqwx", json.dumps(doc))
        self.assertIn("t.amount <= 1000000", doc["policies"][0]["condition"])


if __name__ == "__main__":
    unittest.main()
