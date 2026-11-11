"""Turnkey server-side signer for one sign-gate ticket. DISABLED by default. Never broadcasts.

This is the single module in tools/ that can obtain a signature. It sends ONE
stamped ACTIVITY_TYPE_SIGN_TRANSACTION_V2 request to Turnkey through an
INJECTED transport, verifies the returned signed transaction, logs a `sign`
entry and stops. This module has no send path; a signed transaction is left unsent
(until tools/turnkey_broadcast.py is run with both gates) or until its blockhash expires.

Preconditions, all fail closed (bridge stop rule; network loss -> KILL):
 1. kill switch file absent;
 2. config/turnkey/signer.json "enabled": true, caps valid ("broadcast" must be a boolean but is ignored here);
 3. env TURNKEY_API_PUBLIC_KEY / TURNKEY_API_PRIVATE_KEY / TURNKEY_ORG_ID / TURNKEY_SIGN_WITH;
 4. the ticket is the one the sign gate wrote to data/sign-gate/consumed/<message_sha256>
    (O_EXCL marker) and matches the handoff; now < expires_at_epoch (20 s TTL);
 5. single use: data/turnkey-signer/used/<ticket_id> created with O_EXCL;
 6. sha256(unsigned.b64) == ticket.unsigned_sha256 and sha256(message) == ticket.message_sha256;
 7. the handoff's wallet_profile is "turnkey" (config/wallet.json) and fee payer ==
    TURNKEY_SIGN_WITH == that profile's address (9Ao3...);
 8. local caps: SOL spent by the tx (top-level System transfers from the payer and
    Jupiter route_v2 in_amount) <= per_trade_max_lamports, and today's (Kyiv)
    reserved total + this <= daily_max_lamports. The daily reservation is written
    before the request and never refunded;
 9. Turnkey answers HTTP 200 with ACTIVITY_STATUS_COMPLETED (no polling, no retry);
10. the signed transaction carries the identical message and a valid ed25519
    signature by the fee payer;
11. before the request, the local policy mirror (tools/turnkey_policy_mirror.py) must say Turnkey's
    swap policy would match this exact transaction (local_policy_mismatch otherwise).

Key source: env TURNKEY_API_PUBLIC_KEY + TURNKEY_API_PRIVATE_KEY (hex), or a PKCS8 PEM file
(env TURNKEY_API_KEY_PATH, else config "api_key_path"); the private key is never printed or logged.
Org id: env TURNKEY_ORG_ID else config "organization_id". Signer address: env TURNKEY_SIGN_WITH else the
wallet profile "turnkey". A policy denial (HTTP 403, code 7 / PolicyEnginePermissionError) is the stop
reason `turnkey_policy_denied` (no retry, fail closed). Config "broadcast" is NOT read here: this module
never broadcasts; tools/turnkey_broadcast.py is the only sender and needs it true plus --broadcast-confirm.

Stamping (docs.turnkey.com/developer-reference/api-overview/stamps): ECDSA P-256
SHA-256 over the exact JSON body, DER, hex; X-Stamp = base64url(JSON
{publicKey, scheme: SIGNATURE_SCHEME_TK_API_P256, signature}). Uses the
`cryptography` package when installed; otherwise inject a stamper.
"""

import base64
import binascii
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import socket
import sys
import time
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from metis_offline.base58 import b58encode, pubkey  # noqa: E402
from metis_offline.preflight import JUPITER, MAX_AMOUNT_LAMPORTS, ROUTE_V2, SYSTEM  # noqa: E402
from metis_offline.wire import WireError, parse_transaction  # noqa: E402
from tools import oplog, sign_gate, turnkey_policy_mirror as mirror, wallet  # noqa: E402
from tools.bridge import STATE_DIR as BRIDGE_STATE_DIR, Stop  # noqa: E402

CONFIG_PATH = ROOT / "config" / "turnkey" / "signer.json"
STATE_DIR = ROOT / "data" / "turnkey-signer"
SIGN_PATH = "/public/v1/submit/sign_transaction"
ACTIVITY_TYPE = "ACTIVITY_TYPE_SIGN_TRANSACTION_V2"
SCHEME = "SIGNATURE_SCHEME_TK_API_P256"
ENV_KEYS = ("TURNKEY_API_PUBLIC_KEY", "TURNKEY_API_PRIVATE_KEY", "TURNKEY_ORG_ID", "TURNKEY_SIGN_WITH")
USER_AGENT = "turnkey-check/1.0"  # Turnkey's Cloudflare front end answers error 1010 to the default Python-urllib agent
DEFAULT_ORG_ID = ""  # taken from config/turnkey/signer.json "organization_id" or env TURNKEY_ORG_ID
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
OUTCOME_RE = re.compile(r"OUTCOME_[A-Z_]+")
NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError)


class ConfigError(ValueError):
    pass


class StamperUnavailable(RuntimeError):
    pass


def load_config(path: Path = CONFIG_PATH) -> dict:
    try:
        cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ConfigError("config_unreadable")
    if type(cfg.get("broadcast")) is not bool:
        raise ConfigError("bad_broadcast_flag")
    for key in ("per_trade_max_lamports", "daily_max_lamports"):
        if type(cfg.get(key)) is not int or cfg[key] <= 0:
            raise ConfigError(f"bad_{key}")
    if cfg["per_trade_max_lamports"] > MAX_AMOUNT_LAMPORTS:
        raise ConfigError("per_trade_above_preflight_cap")
    if cfg["daily_max_lamports"] < cfg["per_trade_max_lamports"]:
        raise ConfigError("daily_below_per_trade")
    return cfg


def load_api_key_pem(path) -> tuple[str, str]:
    """(compressed public key hex, private scalar hex) from a PKCS8/SEC1 PEM P-256 key. Never prints either."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    try:
        key = serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
    except (OSError, ValueError, TypeError):
        raise ConfigError("api_key_unreadable")
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ConfigError("api_key_not_p256")
    public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.CompressedPoint).hex()
    return public, format(key.private_numbers().private_value, "064x")


def public_key_hex(path) -> str:
    return load_api_key_pem(path)[0]


class Credentials:
    def __init__(self, api_public_key: str, api_private_key: str, organization_id: str, sign_with: str):
        if not re.fullmatch(r"0[23][0-9a-f]{64}", api_public_key or ""):
            raise ConfigError("bad_api_public_key")
        if not re.fullmatch(r"[0-9a-f]{64}", api_private_key or ""):
            raise ConfigError("bad_api_private_key")
        if not organization_id:
            raise ConfigError("missing_org_id")
        try:
            pubkey(sign_with)
        except Exception:
            raise ConfigError("bad_sign_with")
        self.api_public_key, self._private, self.organization_id, self.sign_with = api_public_key, api_private_key, organization_id, sign_with

    @classmethod
    def from_env(cls, env=os.environ) -> "Credentials":
        missing = [k for k in ENV_KEYS if not env.get(k)]
        if missing:
            raise ConfigError("missing_env:" + ",".join(missing))
        return cls(*(env[k] for k in ENV_KEYS))

    @classmethod
    def resolve(cls, config: dict, env=os.environ, wallet_path: Path = wallet.CONFIG_PATH) -> "Credentials":
        """Hex keys from env, else the PEM file (env TURNKEY_API_KEY_PATH / config api_key_path).
        Org: env TURNKEY_ORG_ID / config organization_id. Signer: env TURNKEY_SIGN_WITH / wallet profile turnkey."""
        org = env.get("TURNKEY_ORG_ID") or config.get("organization_id")
        sign_with = env.get("TURNKEY_SIGN_WITH") or wallet.load_profile("turnkey", path=wallet_path).address
        if env.get("TURNKEY_API_PUBLIC_KEY") or env.get("TURNKEY_API_PRIVATE_KEY"):
            missing = [k for k in ("TURNKEY_API_PUBLIC_KEY", "TURNKEY_API_PRIVATE_KEY") if not env.get(k)]
            if missing or not org:
                raise ConfigError("missing_env:" + ",".join(missing + ([] if org else ["TURNKEY_ORG_ID"])))
            return cls(env["TURNKEY_API_PUBLIC_KEY"], env["TURNKEY_API_PRIVATE_KEY"], org, sign_with)
        key_path = env.get("TURNKEY_API_KEY_PATH") or config.get("api_key_path")
        if not key_path or not org:
            raise ConfigError("missing_env:" + ",".join((["TURNKEY_API_KEY_PATH"] if not key_path else []) + (["TURNKEY_ORG_ID"] if not org else [])))
        public, private = load_api_key_pem(os.path.expanduser(key_path))
        return cls(public, private, org, sign_with)

    def __repr__(self) -> str:  # never show the private key
        return f"Credentials(public={self.api_public_key[:10]}..., org={self.organization_id}, sign_with={self.sign_with})"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def p256_stamp(body: str, api_public_key: str, api_private_key: str) -> str:
    """Return the X-Stamp header value for BODY. Requires `cryptography`."""
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
    except ImportError:
        raise StamperUnavailable("cryptography package not installed; inject a stamper")
    key = ec.derive_private_key(int(api_private_key, 16), ec.SECP256R1())
    derived = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.CompressedPoint).hex()
    if derived != api_public_key:
        raise StamperUnavailable("api key pair mismatch")
    signature = key.sign(body.encode("utf-8"), ec.ECDSA(hashes.SHA256())).hex()
    stamp = {"publicKey": api_public_key, "scheme": SCHEME, "signature": signature}
    return _b64url(json.dumps(stamp, separators=(",", ":")).encode())


def verify_stamp(body: str, stamp_header: str) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec

    stamp = json.loads(base64.urlsafe_b64decode(stamp_header + "=" * (-len(stamp_header) % 4)))
    if stamp.get("scheme") != SCHEME:
        return False
    key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), bytes.fromhex(stamp["publicKey"]))
    try:
        key.verify(bytes.fromhex(stamp["signature"]), body.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
        return True
    except InvalidSignature:
        return False


def sign_request_body(creds: Credentials, unsigned_raw: bytes, timestamp_ms: int) -> str:
    """JSON body for ACTIVITY_TYPE_SIGN_TRANSACTION_V2; the unsigned transaction is hex (as @turnkey/solana sends it)."""
    return json.dumps({
        "type": ACTIVITY_TYPE, "timestampMs": str(timestamp_ms), "organizationId": creds.organization_id,
        "parameters": {"signWith": creds.sign_with, "unsignedTransaction": unsigned_raw.hex(), "type": "TRANSACTION_TYPE_SOLANA"},
    }, separators=(",", ":"))


def urllib_transport(url: str, body: str, headers: dict, timeout: float) -> tuple[int, dict]:
    """Real HTTPS transport (config transport == "https" and enabled). Returns (HTTP status, JSON object);
    a non-JSON or non-object body (e.g. a Cloudflare page) comes back as {"_unparsed": first 200 chars}."""
    import urllib.request

    req = urllib.request.Request(url, data=body.encode("utf-8"), headers={"User-Agent": USER_AGENT, **headers}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, text = resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        status, text = exc.code, exc.read().decode("utf-8", "replace")
    try:
        payload = json.loads(text or "{}")
    except ValueError:
        payload = None
    return status, payload if isinstance(payload, dict) else {"_unparsed": text[:200]}


def is_policy_denial(status: int, payload) -> bool:
    """Turnkey's policy engine refusal: HTTP 403, gRPC code 7 (PermissionDenied), PolicyEnginePermissionError."""
    if status != 403 or not isinstance(payload, dict):
        return False
    return payload.get("code") == 7 or "PolicyEnginePermissionError" in json.dumps(payload, default=str)


def denial_facts(payload: dict) -> dict:
    text = json.dumps(payload, default=str)
    policy = UUID_RE.search(text)
    outcome = OUTCOME_RE.search(text)
    return {"policy_id": policy.group(0) if policy else None, "outcome": outcome.group(0) if outcome else None}


def tx_spend_lamports(tx, payer: bytes) -> int:
    """SOL the payer commits: top-level System transfers from the payer, or Jupiter route_v2 in_amount if larger."""
    keys = list(tx.account_keys)
    transfers = route_in = 0
    for ix in tx.instructions:
        program = keys[ix.program_index] if ix.program_index < len(keys) else None
        if program == SYSTEM and ix.data[:4] in (b"\x02\0\0\0",) and len(ix.data) >= 12:
            if ix.accounts and ix.accounts[0] < len(keys) and keys[ix.accounts[0]] == payer:
                transfers += int.from_bytes(ix.data[4:12], "little")
        elif program == JUPITER and ix.data[:8] == ROUTE_V2 and len(ix.data) >= 16:
            route_in += int.from_bytes(ix.data[8:16], "little")
    return max(transfers, route_in)


def _ed25519_ok(public: bytes, signature: bytes, message: bytes) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        Ed25519PublicKey.from_public_bytes(public).verify(signature, message)
        return True
    except InvalidSignature:
        return False


class TurnkeySigner:
    def __init__(self, config: dict, credentials: Credentials, transport, *, stamper=p256_stamp, clock=time.time,
                 state_dir: Path = STATE_DIR, kill_file: Path = BRIDGE_STATE_DIR / "KILL", gate_state_dir: Path = sign_gate.STATE_DIR,
                 log_path: Path | None = None, sig_verify=_ed25519_ok, wallet_path: Path = wallet.CONFIG_PATH):
        self.config, self.creds, self.transport, self.stamper, self.clock = config, credentials, transport, stamper, clock
        self.state_dir, self.kill_file, self.gate_state_dir, self.log_path = Path(state_dir), Path(kill_file), Path(gate_state_dir), log_path
        self.sig_verify = sig_verify
        self.profile = wallet.load_profile("turnkey", path=wallet_path)
        if self.creds.sign_with != self.profile.address:
            raise ConfigError("sign_with_not_turnkey_profile")

    @classmethod
    def from_environment(cls, config_path: Path = CONFIG_PATH, env=os.environ, **kwargs) -> "TurnkeySigner":
        cfg = load_config(config_path)
        if cfg.get("enabled") is not True:
            raise ConfigError("signer_disabled")
        creds = Credentials.resolve(cfg, env, kwargs.get("wallet_path", wallet.CONFIG_PATH))
        transport = kwargs.pop("transport", None)
        if transport is None:
            if cfg.get("transport") != "https":
                raise ConfigError("transport_not_configured")
            transport = urllib_transport
        return cls(cfg, creds, transport, **kwargs)

    # --- helpers -------------------------------------------------------------
    def _ticket(self, handoff: dict) -> dict:
        given = handoff.get("ticket") or {}
        sha = given.get("message_sha256")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise Stop("ticket_missing")
        try:
            issued = json.loads((self.gate_state_dir / "consumed" / sha).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise Stop("ticket_not_issued_by_gate", message_sha256=sha)
        for key in ("ticket_id", "message_sha256", "unsigned_sha256", "expires_at_epoch", "blockhash", "last_valid_block_height"):
            if issued.get(key) != given.get(key):
                raise Stop("ticket_mismatch", field=key, message_sha256=sha)
        return issued

    def _use_once(self, ticket: dict) -> None:
        used = self.state_dir / "used"
        used.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(used / ticket["ticket_id"], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            raise Stop("ticket_reused", message_sha256=ticket["message_sha256"])
        os.write(fd, json.dumps({"used_at": self.clock(), "message_sha256": ticket["message_sha256"]}).encode())
        os.close(fd)

    def _reserve_daily(self, lamports: int) -> int:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        day = dt.datetime.fromtimestamp(self.clock(), oplog.KYIV).date().isoformat()
        path = self.state_dir / "daily.json"
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            raw = os.pread(fd, 1 << 20, 0)
            ledger = json.loads(raw) if raw.strip() else {}
            spent = int(ledger.get(day, 0))
            if spent + lamports > self.config["daily_max_lamports"]:
                raise Stop("daily_cap", spent_today=spent, requested=lamports, daily_max=self.config["daily_max_lamports"])
            ledger[day] = spent + lamports
            data = json.dumps(ledger, sort_keys=True).encode()
            os.ftruncate(fd, 0)
            os.pwrite(fd, data, 0)
            os.fsync(fd)
            return ledger[day]
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    # --- main ----------------------------------------------------------------
    def __call__(self, handoff: dict) -> dict:
        if self.kill_file.exists():
            raise Stop("kill_switch", halt=True)
        if self.config.get("enabled") is not True:
            raise Stop("signer_disabled")
        if handoff.get("wallet_profile") != "turnkey" or handoff.get("signer") != self.profile.address:
            raise Stop("wallet_profile_not_turnkey", wallet_profile=handoff.get("wallet_profile"))
        ticket = self._ticket(handoff)
        sha = ticket["message_sha256"]
        if self.clock() >= ticket["expires_at_epoch"]:
            raise Stop("ticket_expired", message_sha256=sha)
        self._use_once(ticket)
        try:
            wire_b64 = Path(handoff.get("unsigned_b64_path") or "").read_text(encoding="utf-8").strip()
        except OSError:
            raise Stop("unsigned_missing", message_sha256=sha)
        if hashlib.sha256(wire_b64.encode("ascii")).hexdigest() != ticket["unsigned_sha256"]:
            raise Stop("unsigned_mismatch", message_sha256=sha)
        try:
            raw = base64.b64decode(wire_b64, validate=True)
            tx = parse_transaction(raw)
        except (binascii.Error, ValueError, WireError):
            raise Stop("unsigned_unparseable", message_sha256=sha)
        if hashlib.sha256(tx.message).hexdigest() != sha:
            raise Stop("message_hash_mismatch", message_sha256=sha)
        payer = tx.account_keys[0]
        if b58encode(payer) != self.creds.sign_with or b58encode(payer) != self.profile.address:
            raise Stop("fee_payer_mismatch", message_sha256=sha, fee_payer=b58encode(payer))
        spend = tx_spend_lamports(tx, payer)
        if spend <= 0 or spend > self.config["per_trade_max_lamports"]:
            raise Stop("per_trade_cap", spend_lamports=spend, per_trade_max=self.config["per_trade_max_lamports"], message_sha256=sha)
        mismatches = mirror.swap_failures(tx, self.profile.address, self.profile.wsol_ata, self.profile.usdc_ata)
        if mismatches:
            raise Stop("local_policy_mismatch", message_sha256=sha, mismatches=mismatches)
        spent_today = self._reserve_daily(spend)
        now_ms = int(self.clock() * 1000)
        body = sign_request_body(self.creds, raw, now_ms)
        try:
            stamp = self.stamper(body, self.creds.api_public_key, self.creds._private)
        except StamperUnavailable:
            raise Stop("stamper_unavailable", message_sha256=sha)
        if self.clock() >= ticket["expires_at_epoch"]:
            raise Stop("ticket_expired", message_sha256=sha)
        url = self.config.get("api_base", "https://api.turnkey.com").rstrip("/") + SIGN_PATH
        headers = {"Content-Type": "application/json", "X-Stamp": stamp, "User-Agent": USER_AGENT}
        oplog.append("note", {"component": "turnkey_signer", "event": "request", "ticket_id": ticket["ticket_id"], "spend_lamports": spend,
                              "spent_today_lamports": spent_today, "sign_with": self.creds.sign_with, "timestamp_ms": str(now_ms)}, sha, path=self.log_path)
        try:
            status, payload = self.transport(url, body, headers, float(self.config.get("request_timeout_s", 10)))
        except NETWORK_ERRORS as exc:
            raise Stop("turnkey_network_loss", halt=True, error_type=type(exc).__name__, message_sha256=sha)
        except Exception as exc:  # noqa: BLE001
            raise Stop("turnkey_transport_exception", halt=True, error_type=type(exc).__name__, message_sha256=sha)
        if is_policy_denial(status, payload):
            raise Stop("turnkey_policy_denied", message_sha256=sha, http_status=status, **denial_facts(payload))
        if status != 200:
            halt = status == 429 or status >= 500
            raise Stop(f"turnkey_http_{status}", halt=halt, message_sha256=sha, turnkey_message=str((payload or {}).get("message", ""))[:200])
        activity = payload.get("activity") if isinstance(payload, dict) else None
        if not isinstance(activity, dict) or not isinstance(activity.get("status"), str):
            raise Stop("turnkey_malformed_response", message_sha256=sha)
        if activity.get("status") == "ACTIVITY_STATUS_REJECTED":
            raise Stop("turnkey_policy_denied", message_sha256=sha, activity_id=activity.get("id"), outcome="ACTIVITY_STATUS_REJECTED")
        if activity.get("status") != "ACTIVITY_STATUS_COMPLETED":
            raise Stop(f"turnkey_status:{activity.get('status')}", message_sha256=sha, activity_id=activity.get("id"),
                       failure=str(activity.get("failure") or "")[:200])
        signed_hex = (((activity.get("result") or {}).get("signTransactionResult") or {}).get("signedTransaction"))
        try:
            signed = parse_transaction(bytes.fromhex(signed_hex))
        except (TypeError, ValueError, WireError, IndexError):
            raise Stop("signed_unparseable", message_sha256=sha, activity_id=activity.get("id"))
        if hashlib.sha256(signed.message).hexdigest() != sha:
            raise Stop("signed_message_mismatch", message_sha256=sha, activity_id=activity.get("id"))
        if not signed.signatures or not self.sig_verify(payer, signed.signatures[0], signed.message):
            raise Stop("signed_signature_invalid", message_sha256=sha, activity_id=activity.get("id"))
        signature = b58encode(signed.signatures[0])
        out = Path(handoff["unsigned_b64_path"]).with_name("turnkey-signed.hex")
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(signed_hex + "\n")
        oplog.append("sign", {"mechanism": "turnkey ACTIVITY_TYPE_SIGN_TRANSACTION_V2", "signer": self.creds.sign_with, "signature": signature,
                              "ticket_id": ticket["ticket_id"], "activity_id": activity.get("id"), "broadcast": False}, sha, path=self.log_path)
        return {"status": "signed_not_broadcast", "signature": signature, "activity_id": activity.get("id"), "signed_path": str(out),
                "ticket_id": ticket["ticket_id"], "message_sha256": sha}
