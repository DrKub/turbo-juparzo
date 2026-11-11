"""Turnkey signing transport, policy mirror, key helpers, CLI and broadcast gate. Mocked HTTP and RPC only:
no Turnkey call, no Solana call, no key from the box."""

import base64
import hashlib
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import test_turnkey_signer as base
from metis_offline import fixtures as fx
from metis_offline.base58 import b58encode
from metis_offline.wire import parse_transaction, serialize
from tools import bridge, oplog, turnkey_broadcast as tb, turnkey_cli as cli, turnkey_policy_mirror as mirror, turnkey_signer as ts

DENIAL = {"code": 7, "message": "Turnkey error 7: policy engine denied the request (PolicyEnginePermissionError): "
                                 "policy 8eab8620-1109-46f1-9986-45d79ba13530 outcome OUTCOME_DENY_EXPLICIT",
          "details": []}


class FakeResponse:
    def __init__(self, status, body):
        self.status, self._body = status, body if isinstance(body, bytes) else json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(status, body):
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    return urllib.error.HTTPError("https://api.turnkey.com", status, "x", {}, io.BytesIO(data))


def signed_response(body, key=base.WALLET, tamper=None):
    """What Turnkey returns: the transaction with the wallet's ed25519 signature."""
    tx = parse_transaction(bytes.fromhex(json.loads(body)["parameters"]["unsignedTransaction"]))
    signed = tx.__class__(*((key.sign(tx.message),),) + tuple(getattr(tx, f) for f in list(tx.__dataclass_fields__)[1:]))
    raw = serialize(signed)
    if tamper:
        raw = tamper(raw)
    return {"activity": {"id": "act-9", "status": "ACTIVITY_STATUS_COMPLETED",
                         "result": {"signTransactionResult": {"signedTransaction": raw.hex()}}}}


class Env(unittest.TestCase):
    """SignerTest helpers by composition (importing the class itself would re-run its tests here)."""
    setUp, tearDown, clock = base.SignerTest.setUp, base.SignerTest.tearDown, base.SignerTest.clock
    wallet_file, handoff, signer = base.SignerTest.wallet_file, base.SignerTest.handoff, base.SignerTest.signer

    def real_transport_signer(self, urlopen, **kw):
        """TurnkeySigner wired to the REAL urllib_transport with urllib.request.urlopen mocked."""
        self.urlopen = urlopen
        return self.signer(ts.urllib_transport, cfg=base.config(daily_max_lamports=10_000_000), **kw)

    def run_signer(self, handler, ticket_id="t1"):
        with mock.patch("urllib.request.urlopen", side_effect=handler) as m:
            signer = self.real_transport_signer(m)
            try:
                return signer(self.handoff(ticket_id=ticket_id)), m
            except bridge.Stop as exc:
                return exc, m


class TransportTest(Env):
    def test_success_through_real_transport_with_user_agent_and_valid_stamp(self):
        def handler(req, timeout=None):
            self.sent = req
            return FakeResponse(200, signed_response(req.data.decode()))
        out, m = self.run_signer(handler)
        self.assertEqual(out["status"], "signed_not_broadcast")
        self.assertEqual(m.call_count, 1)
        req = self.sent
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.full_url, "https://api.turnkey.com/public/v1/submit/sign_transaction")
        headers = {k.lower(): v for k, v in req.header_items()}
        self.assertEqual(headers["user-agent"], "turnkey-check/1.0")
        self.assertEqual(headers["content-type"], "application/json")
        self.assertTrue(ts.verify_stamp(req.data.decode(), headers["x-stamp"]))
        self.assertEqual(Path(out["signed_path"]).stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(bytes.fromhex(json.loads(req.data)["parameters"]["unsignedTransaction"])), len(Path(out["signed_path"]).read_text().strip()) // 2)

    def test_policy_denied_403_code_7_is_a_clear_fail_closed_result(self):
        out, m = self.run_signer(lambda req, timeout=None: (_ for _ in ()).throw(http_error(403, DENIAL)))
        self.assertIsInstance(out, bridge.Stop)
        self.assertEqual((out.reason, out.halt), ("turnkey_policy_denied", False))
        self.assertEqual((out.facts["policy_id"], out.facts["outcome"], out.facts["http_status"]),
                         ("8eab8620-1109-46f1-9986-45d79ba13530", "OUTCOME_DENY_EXPLICIT", 403))
        self.assertEqual(m.call_count, 1)  # never retried
        self.assertFalse((self.dir / "build-t1" / "turnkey-signed.hex").exists())
        again = self.signer(ts.urllib_transport, cfg=base.config(daily_max_lamports=10_000_000))
        with self.assertRaises(bridge.Stop) as ctx:
            again(self.handoff(ticket_id="t1"))
        self.assertEqual(ctx.exception.reason, "ticket_reused")
        self.assertEqual([e for e in oplog.read(self.log) if e["op"] == "sign"], [])
        self.assertEqual(json.loads((self.dir / "tk" / "daily.json").read_text()).popitem()[1], 1_000_000)  # reservation kept

    def test_policy_denied_recognised_by_error_name_alone_and_other_403s_are_not(self):
        self.assertTrue(ts.is_policy_denial(403, {"message": "PolicyEnginePermissionError: no policy"}))
        self.assertTrue(ts.is_policy_denial(403, {"code": 7}))
        self.assertFalse(ts.is_policy_denial(401, {"code": 7}))
        self.assertFalse(ts.is_policy_denial(403, {"_unparsed": "error code: 1010"}))
        out, _ = self.run_signer(lambda req, timeout=None: (_ for _ in ()).throw(http_error(403, b"<html>error code: 1010</html>")), ticket_id="cf")
        self.assertEqual(out.reason, "turnkey_http_403")

    def test_rejected_activity_is_policy_denied(self):
        out, _ = self.run_signer(lambda req, timeout=None: FakeResponse(200, {"activity": {"id": "a", "status": "ACTIVITY_STATUS_REJECTED"}}))
        self.assertEqual(out.reason, "turnkey_policy_denied")

    def test_malformed_responses_stop(self):
        completed = lambda result: {"activity": {"id": "a", "status": "ACTIVITY_STATUS_COMPLETED", "result": result}}  # noqa: E731
        cases = [(b"<html>not json</html>", "turnkey_malformed_response"), ({}, "turnkey_malformed_response"),
                 ({"activity": "x"}, "turnkey_malformed_response"), ({"activity": {"id": "a"}}, "turnkey_malformed_response"),
                 ([1, 2], "turnkey_malformed_response"), (completed({}), "signed_unparseable"),
                 (completed({"signTransactionResult": {"signedTransaction": "zz"}}), "signed_unparseable"),
                 (completed({"signTransactionResult": {"signedTransaction": "00"}}), "signed_unparseable"),
                 (completed({"signTransactionResult": {"signedTransaction": 5}}), "signed_unparseable")]
        for n, (body, reason) in enumerate(cases):
            with self.subTest(body=str(body)[:40]):
                out, _ = self.run_signer(lambda req, timeout=None, b=body: FakeResponse(200, b), ticket_id=f"m{n}")
                self.assertIsInstance(out, bridge.Stop)
                self.assertEqual(out.reason, reason)
                self.assertFalse(out.halt)

    def test_signature_mismatch_and_altered_message(self):
        other = Ed25519PrivateKey.from_private_bytes(bytes([7]) * 32)
        out, _ = self.run_signer(lambda req, timeout=None: FakeResponse(200, signed_response(req.data.decode(), key=other)), ticket_id="sig")
        self.assertEqual(out.reason, "signed_signature_invalid")

        def alter_blockhash(raw):  # same length, parses, but the message is no longer the one we asked to sign
            i = raw.index(fx.BLOCKHASH)
            return raw[:i] + bytes([fx.BLOCKHASH[0] ^ 1]) + raw[i + 1:]
        out, _ = self.run_signer(lambda req, timeout=None: FakeResponse(200, signed_response(req.data.decode(), tamper=alter_blockhash)), ticket_id="alt")
        self.assertEqual(out.reason, "signed_message_mismatch")

        def alter_amount(raw):  # a signature over the ALTERED message is valid for it, yet must still be refused
            tx = parse_transaction(raw)
            data = bytearray(tx.instructions[3].data)
            data[4] ^= 1
            ixs = list(tx.instructions)
            ixs[3] = ixs[3].__class__(ixs[3].program_index, ixs[3].accounts, bytes(data))
            fields = {f: getattr(tx, f) for f in tx.__dataclass_fields__}
            fields["instructions"] = tuple(ixs)
            altered = tx.__class__(**fields)
            fields["signatures"] = (base.WALLET.sign(parse_transaction(serialize(altered)).message),)
            return serialize(tx.__class__(**fields))
        out, _ = self.run_signer(lambda req, timeout=None: FakeResponse(200, signed_response(req.data.decode(), tamper=alter_amount)), ticket_id="amt")
        self.assertEqual(out.reason, "signed_message_mismatch")
        self.assertEqual([e for e in oplog.read(self.log) if e["op"] == "sign"], [])
        self.assertFalse(list(self.dir.glob("build-*/turnkey-signed.hex")))

    def test_network_failure_halts_and_unparsed_body_is_not_trusted(self):
        out, _ = self.run_signer(lambda req, timeout=None: (_ for _ in ()).throw(urllib.error.URLError("down")))
        self.assertEqual((out.reason, out.halt), ("turnkey_network_loss", True))
        status, payload = None, None
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(200, b"plain text")):
            status, payload = ts.urllib_transport("https://x", "{}", {}, 1)
        self.assertEqual((status, payload), (200, {"_unparsed": "plain text"}))


class MirrorAndSignerGuardTest(Env):
    def tx(self, **kw):
        return parse_transaction(base.case_for(base.WALLET_PUB, **kw).raw())

    def args(self):
        p = ts.wallet.load_profile("turnkey", path=self.wallet_file())
        return p.address, p.wsol_ata, p.usdc_ata

    def test_mirror_accepts_a_good_swap_and_rejects_policy_violations(self):
        self.assertEqual(mirror.swap_failures(self.tx(), *self.args()), [])
        self.assertIn("transfer_not_signer_to_wsol_ata_within_cap", mirror.swap_failures(self.tx(lamports=1_000_001), *self.args()))
        signer, wsol, usdc = self.args()
        self.assertIn("deny_transfer_to_other_destination", mirror.swap_failures(self.tx(), signer, b58encode(bytes(32)), usdc))
        self.assertIn("jupiter_accounts_not_signer_wsol_usdc", mirror.swap_failures(self.tx(), signer, wsol, b58encode(bytes([5]) * 32)))
        case = base.case_for(base.WALLET_PUB)
        case.instructions = case.instructions + [fx.cu_limit_ix(1), fx.cu_limit_ix(2), fx.cu_limit_ix(3)]
        self.assertIn("too_many_instructions", mirror.swap_failures(parse_transaction(case.raw()), *self.args()))
        case = base.case_for(base.WALLET_PUB)
        case.instructions = [ix for ix in case.instructions if ix.program_index != fx.I_JUP]
        self.assertIn("jupiter_instruction_count_not_1", mirror.swap_failures(parse_transaction(case.raw()), *self.args()))
        case = base.case_for(base.WALLET_PUB)
        case.instructions = case.instructions + [fx.CompiledInstruction(fx.I_TOKEN, (fx.I_WSOL_ATA, fx.I_USDC_ATA, fx.I_USER), bytes([3]) + bytes(8))]
        self.assertIn("spl_transfer_present", mirror.swap_failures(parse_transaction(case.raw()), *self.args()))
        case = base.case_for(base.WALLET_PUB)
        case.version, case.header, case.lookups = "legacy", (1, 0, 6), []
        self.assertIn("version_not_v0", mirror.swap_failures(parse_transaction(case.raw()), *self.args()))

    def test_mirror_constants_match_the_policy_file(self):
        doc = json.loads((Path(ts.ROOT) / "config" / "turnkey" / "policies.json").read_text())
        swap = doc["policies"][0]["condition"]
        for program in mirror.ALLOWED_PROGRAMS:
            self.assertIn(f"'{program}'", swap)
        self.assertEqual(len(mirror.ALLOWED_PROGRAMS), swap.split("program_keys.all(p, p in [")[1].split("]")[0].count("'") // 2)
        self.assertIn(f"instructions.count() <= {mirror.MAX_INSTRUCTIONS}", swap)
        self.assertIn(f"t.amount <= {mirror.MAX_TRANSFER_LAMPORTS}", swap)
        self.assertIn(f"instruction_data_hex[0..16] == '{mirror.ROUTE_V2_PREFIX_HEX}'", swap)
        self.assertEqual(bytes.fromhex(mirror.ROUTE_V2_PREFIX_HEX), ts.ROUTE_V2)
        self.assertIn(mirror.OWNER_WITHDRAW, json.dumps(doc["policies"][1:3]))
        profile = ts.wallet.load_profile("turnkey")
        for value in (profile.address, profile.wsol_ata, profile.usdc_ata):
            self.assertIn(value, swap)

    def test_signer_stops_locally_before_any_request_when_the_mirror_disagrees(self):
        s = self.signer(base.FakeTransport(), cfg=base.config(daily_max_lamports=10_000_000))
        h = self.handoff(ticket_id="mm")
        # a different destination for the System transfer: the amount cap passes, Turnkey's policy would deny it
        case = base.case_for(base.WALLET_PUB)
        case.instructions = [fx.transfer_ix(fx.AMOUNT, fx.I_USDC_ATA) if ix.program_index == fx.I_SYSTEM else ix for ix in case.instructions]
        raw = case.raw()
        wire = base.base64.b64encode(raw).decode()
        Path(h["unsigned_b64_path"]).write_text(wire)
        sha = hashlib.sha256(parse_transaction(raw).message).hexdigest()
        ticket = json.loads((self.gate / "consumed" / h["ticket"]["message_sha256"]).read_text())
        ticket.update(message_sha256=sha, unsigned_sha256=hashlib.sha256(wire.encode()).hexdigest())
        (self.gate / "consumed" / sha).write_text(json.dumps(ticket))
        h["ticket"] = dict(ticket)
        with self.assertRaises(bridge.Stop) as ctx:
            s(h)
        self.assertEqual(ctx.exception.reason, "local_policy_mismatch")
        self.assertEqual(self.transport.calls, [])
        self.assertFalse((self.dir / "tk" / "daily.json").exists())  # nothing reserved


class KeyAndCliTest(Env):
    def pem(self, key=None, name="key.pem"):
        key = key or ec.generate_private_key(ec.SECP256R1())
        path = self.dir / name
        path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        return key, path

    def test_pem_loading_pubkey_and_no_secret_in_output(self):
        key, path = self.pem()
        scalar = format(key.private_numbers().private_value, "064x")
        public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.CompressedPoint).hex()
        self.assertEqual(ts.load_api_key_pem(path), (public, scalar))
        lines = []
        self.assertEqual(cli.main(["pubkey", "--key", str(path)], out=lines.append), 0)
        self.assertEqual(lines, [public])
        self.assertNotIn(scalar, "".join(lines))
        creds = ts.Credentials.resolve({"organization_id": "org-1", "api_key_path": str(path)}, env={})
        self.assertEqual((creds.api_public_key, creds.organization_id), (public, "org-1"))
        self.assertEqual(creds.sign_with, "9Ao3ggbuUaiWPzYKr4VdsNQ9LMzM6MPmraZRVv4gv1UW")
        self.assertNotIn(scalar, repr(creds))
        env = {"TURNKEY_API_KEY_PATH": str(path), "TURNKEY_ORG_ID": "org-2"}
        self.assertEqual(ts.Credentials.resolve({"organization_id": "org-1"}, env=env).organization_id, "org-2")
        with self.assertRaisesRegex(ts.ConfigError, "missing_env:TURNKEY_API_KEY_PATH"):
            ts.Credentials.resolve({"organization_id": "org-1"}, env={})
        with self.assertRaisesRegex(ts.ConfigError, "api_key_unreadable"):
            ts.load_api_key_pem(self.dir / "nope.pem")
        ed = self.dir / "ed.pem"
        ed.write_bytes(Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        with self.assertRaisesRegex(ts.ConfigError, "api_key_not_p256"):
            ts.load_api_key_pem(ed)

    def test_repo_config_default_key_path_and_disabled_state(self):
        cfg = ts.load_config(ts.CONFIG_PATH)
        self.assertEqual((cfg["enabled"], cfg["broadcast"], cfg["transport"]), (False, False, "https"))
        self.assertEqual(cfg["api_key_path"], "/home/box/.config/turnkey/api_private.pem")
        self.assertEqual(cfg["organization_id"], "a1f37a84-6335-49fe-8f4e-cbcd9e12ea37")
        with self.assertRaisesRegex(ts.ConfigError, "signer_disabled"):
            ts.TurnkeySigner.from_environment(ts.CONFIG_PATH, env={})

    def test_check_command_is_one_stamped_read_only_call(self):
        key, path = self.pem()
        calls = []

        def ok(url, body, headers, timeout):
            calls.append((url, body, headers))
            return 200, {"wallets": [{"walletName": "Turbo Jupiter", "walletId": "w-1"}]}
        lines = []
        self.assertEqual(cli.main(["check", "--key", str(path)], transport=ok, out=lines.append), 0)
        url, body, headers = calls[0]
        self.assertEqual(url, "https://api.turnkey.com/public/v1/query/list_wallets")
        self.assertEqual(json.loads(body), {"organizationId": "a1f37a84-6335-49fe-8f4e-cbcd9e12ea37"})
        self.assertTrue(ts.verify_stamp(body, headers["X-Stamp"]))
        self.assertEqual(headers["User-Agent"], "turnkey-check/1.0")
        self.assertEqual(json.loads(lines[0])["wallets"], [{"name": "Turbo Jupiter", "id": "w-1"}])
        scalar = format(key.private_numbers().private_value, "064x")
        self.assertNotIn(scalar, "".join(lines))
        lines.clear()
        unregistered = lambda *a: (401, {"code": 16, "message": "could not find public key in organization"})  # noqa: E731
        self.assertEqual(cli.main(["check", "--key", str(path)], transport=unregistered, out=lines.append), 2)
        self.assertEqual(json.loads(lines[0])["hint"], "key_not_registered_in_turnkey_org")
        self.assertEqual(len(calls), 1)

    def test_sign_command_refuses_while_disabled_and_stops_on_gate_denial(self):
        lines = []
        self.assertEqual(cli.main(["sign"], run_once=lambda *a, **k: self.fail("must not build"), out=lines.append), 1)
        self.assertIn("signer_disabled", lines[0])
        signer = mock.Mock(side_effect=AssertionError("no signing after a gate denial"))
        denied = lambda out, wallet_profile=None: (2, {"decision": "denied", "reason": "simulation_error"})  # noqa: E731
        self.assertEqual(cli.main(["sign"], run_once=denied, signer_factory=lambda: signer, out=lines.append), 2)
        self.assertIn("denied_before_signing", lines[-1])
        signer.assert_not_called()

    def test_sign_command_success_and_policy_denial_exit_codes(self):
        s = self.signer(base.FakeTransport())
        h = self.handoff()
        approved = lambda out, wallet_profile=None: (0, {"decision": "approved", "ticket": h["ticket"], "unsigned_b64_path": h["unsigned_b64_path"], "ticket_path": "/t"})  # noqa: E731
        lines = []
        with mock.patch.object(cli.wallet, "load_profile", return_value=s.profile):
            self.assertEqual(cli.main(["sign"], run_once=approved, signer_factory=lambda: s, out=lines.append), 0)
        done = json.loads(lines[-1])
        self.assertEqual((done["status"], done["broadcast"]), ("signed_not_broadcast", False))
        self.assertNotIn("signedTransaction", lines[-1])
        denied = lambda hh: (_ for _ in ()).throw(bridge.Stop("turnkey_policy_denied", policy_id="p"))  # noqa: E731
        with mock.patch.object(cli.wallet, "load_profile", return_value=s.profile):
            self.assertEqual(cli.main(["sign"], run_once=approved, signer_factory=lambda: denied, out=lines.append), 2)
        self.assertIn("turnkey_policy_denied", lines[-1])


class BroadcastGateTest(Env):
    def prepare(self, **cfg_kw):
        s = self.signer(base.FakeTransport(), cfg=base.config(daily_max_lamports=10_000_000))
        h = self.handoff()
        out = s(h)
        ticket = self.dir / "ticket.json"
        ticket.write_text(json.dumps(h["ticket"]))
        cfg = self.dir / "signer.json"
        cfg.write_text(json.dumps(base.config(**{"broadcast": True, "daily_max_lamports": 10_000_000, **cfg_kw})))
        self.kw = dict(config_path=cfg, kill_file=self.dir / "KILL", state_dir=self.dir / "tk", gate_state_dir=self.gate,
                       wallet_path=self.wallet_file(), log_path=self.log)
        self.rpc_calls = []
        self.sends = []

        def rpc(method, params):
            self.rpc_calls.append(method)
            return {"getBlockHeight": 950, "isBlockhashValid": {"value": self.valid}}[method]
        self.valid = True
        self.rpc = rpc
        self.sig = b58encode(parse_transaction(bytes.fromhex(Path(out["signed_path"]).read_text().strip())).signatures[0])

        def send(wire):
            self.sends.append(wire)
            return self.send_result or self.sig
        self.send, self.send_result = send, None
        return out, ticket

    def go(self, out, ticket, confirm=True, **kw):
        return tb.broadcast(Path(out["signed_path"]), ticket, confirm=confirm, rpc=self.rpc, send=self.send, **{**self.kw, **kw})

    def refused(self, out, ticket, **kw):
        before = len(self.sends)
        with self.assertRaises(tb.BroadcastRefused) as ctx:
            self.go(out, ticket, **kw)
        self.assertEqual(len(self.sends), before)
        return ctx.exception

    def test_both_gates_are_required(self):
        out, ticket = self.prepare(broadcast=False)
        self.assertEqual(self.refused(out, ticket, confirm=True).reason, "broadcast_disabled_in_config")
        self.kw["config_path"].write_text(json.dumps(base.config(broadcast=True, daily_max_lamports=10_000_000)))
        self.assertEqual(self.refused(out, ticket, confirm=False).reason, "broadcast_confirm_missing")
        self.assertEqual(self.refused(out, ticket, confirm=None).reason, "broadcast_confirm_missing")
        self.assertEqual(self.rpc_calls, [])

    def test_repo_config_cannot_broadcast(self):
        out, ticket = self.prepare()
        self.assertEqual(self.refused(out, ticket, config_path=ts.CONFIG_PATH).reason, "broadcast_disabled_in_config")

    def test_cli_needs_the_flag_and_default_send_is_not_called(self):
        out, ticket = self.prepare()
        lines = []
        with mock.patch.object(tb, "send_raw_transaction", side_effect=AssertionError("must not send")):
            code = cli.main(["broadcast", "--signed", out["signed_path"], "--ticket", str(ticket)], out=lines.append, rpc=self.rpc, **self.kw)
        self.assertEqual(code, 2)
        self.assertIn("broadcast_confirm_missing", lines[-1])
        code = cli.main(["broadcast", "--signed", out["signed_path"], "--ticket", str(ticket), "--broadcast-confirm"], send=self.send, out=lines.append, rpc=self.rpc, **self.kw)
        self.assertEqual((code, len(self.sends)), (0, 1))
        self.assertEqual(json.loads(lines[-1])["status"], "broadcast_sent")

    def test_sends_once_after_all_checks_and_never_twice(self):
        out, ticket = self.prepare()
        result = self.go(out, ticket)
        self.assertEqual((result["status"], result["signature"], result["signature_matches_signed_tx"]), ("broadcast_sent", self.sig, True))
        self.assertEqual(len(self.sends), 1)
        self.assertEqual(base64.b64decode(self.sends[0]), bytes.fromhex(Path(out["signed_path"]).read_text().strip()))
        note = [e for e in oplog.read(self.log) if e["data"].get("event") == "broadcast"][0]
        self.assertEqual(note["data"]["signature"], self.sig)
        self.assertEqual(self.refused(out, ticket).reason, "ticket_already_broadcast")

    def test_send_failure_is_not_retried_and_consumes_the_ticket(self):
        out, ticket = self.prepare()

        def boom(wire):
            self.sends.append(wire)
            raise RuntimeError("rpc down")
        with self.assertRaises(RuntimeError):
            tb.broadcast(Path(out["signed_path"]), ticket, confirm=True, rpc=self.rpc, send=boom, **self.kw)
        self.assertEqual(len(self.sends), 1)
        self.assertEqual(self.refused(out, ticket).reason, "ticket_already_broadcast")

    def test_stale_or_unknown_blockhash_is_not_sent(self):
        out, ticket = self.prepare()
        self.valid = False
        self.assertEqual(self.refused(out, ticket).reason, "blockhash_stale")
        self.valid = True
        self.rpc = lambda m, p: 1_000 if m == "getBlockHeight" else {"value": True}  # 0 blocks left
        self.assertEqual(self.refused(out, ticket).reason, "blockhash_stale")
        self.rpc = mock.Mock(side_effect=OSError("down"))
        self.assertEqual(self.refused(out, ticket).reason, "freshness_unknown")
        self.assertFalse((self.dir / "tk" / "broadcast").exists())

    def test_signed_file_must_match_the_ticket_wallet_signature_and_policy(self):
        out, ticket = self.prepare()
        path = Path(out["signed_path"])
        good = path.read_text()
        raw = bytearray(bytes.fromhex(good.strip()))
        raw[1] ^= 1  # corrupt the signature
        path.write_text(bytes(raw).hex())
        self.assertEqual(self.refused(out, ticket).reason, "signed_signature_invalid")
        raw = bytearray(bytes.fromhex(good.strip()))
        i = bytes(raw).index(fx.BLOCKHASH)
        raw[i] ^= 1
        path.write_text(bytes(raw).hex())
        self.assertEqual(self.refused(out, ticket).reason, "signed_message_mismatch")
        path.write_text("not hex")
        self.assertEqual(self.refused(out, ticket).reason, "signed_unreadable")
        path.write_text(good)
        (self.dir / "tk" / "used" / "t1").unlink()
        self.assertEqual(self.refused(out, ticket).reason, "ticket_not_signed_by_turnkey_signer")
        (self.dir / "KILL").write_text("x")
        self.assertEqual(self.refused(out, ticket).reason, "kill_switch")

    def test_returned_signature_mismatch_is_flagged(self):
        out, ticket = self.prepare()
        self.send_result = "5" * 88
        result = self.go(out, ticket)
        self.assertFalse(result["signature_matches_signed_tx"])


if __name__ == "__main__":
    unittest.main()
