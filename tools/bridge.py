"""Scanner -> turbo-juparzo bridge. Never signs, never sends.

usage: python3 tools/bridge.py (--signal FILE | --stdin) [--out DIR] [--fills-dir DIR]
                               [--max-age-s N] [--budget-s N] [--state-dir DIR] [--log-path FILE]

Consumes ONE memedesk signal (the JSON the scanner writes to
/workspace/memedesk/live/signals/NNNN_buy.json or NNNN_sell.json), maps it to a
swap intent, and drives the existing pipeline (tools/live_once.run_once:
build -> metis-offline check -> read-only simulation -> sign gate). The most it
produces is a `ready_to_sign` handoff file with the gate ticket under
STATE_DIR/ready/. Signing is NOT here: only the agent can ask the Phantom
embedded wallet to sign, and it must do so before ticket.expires_at
(sign gate TTL 20 s), then run tools/record_result.py --ticket.

Stop rule (no retry, ever, for a given signal):
- each signal is claimed once by an O_EXCL file STATE_DIR/claims/<key>.json,
  key = id_side_<sha256 of the canonical signal JSON>[:16]. The identical
  signal is refused (exit 4); a new signal for the same coin has a new id and
  created_at and therefore a new key;
- any validation failure, unsupported intent, check rejection, simulation
  error, gate refusal, expired ticket -> claim marked `stopped`, stop record in
  logs/operations.jsonl, notification file in STATE_DIR/notifications/, exit 2;
- network loss, RPC error, unexpected exception or exceeding the wall-clock
  budget -> the same, plus the global kill switch STATE_DIR/KILL is written,
  exit 3. While KILL exists every invocation halts (exit 3) without claiming.
  Only a human removes KILL.
- optional --fills-dir: on a stop, write {"status":"failed","reason":...} as
  NNNN_<side>_fill.json (never overwrites; never writes "filled").

Exit codes: 0 ready_to_sign, 1 usage, 2 stopped, 3 halted, 4 duplicate.
"""

import datetime as dt
import hashlib
import json
import math
import os
import re
import socket
import sys
import time
import urllib.error
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from metis_offline.base58 import b58encode, pubkey  # noqa: E402
from metis_offline.preflight import (  # noqa: E402
    MAX_AMOUNT_LAMPORTS, MAX_PRIORITY_FEE_LAMPORTS, MAX_SLIPPAGE_BPS, WSOL, Intent, Reject, check_intent,
)
from tools import jupiter_unsigned_build as build_mod  # noqa: E402
from tools import oplog  # noqa: E402

STATE_DIR = ROOT / "data" / "bridge"
OUT_ROOT = Path("/tmp/turbo-juparzo-bridge")
MAX_SIGNAL_AGE_S = 600  # memedesk FILL_TIMEOUT / signal_ttl_seconds
FUTURE_SKEW_S = 5
DEFAULT_BUDGET_S = 90
LAMPORTS_PER_SOL = 1_000_000_000
USER = build_mod.USER

EXIT_READY, EXIT_USAGE, EXIT_STOPPED, EXIT_HALTED, EXIT_DUPLICATE = 0, 1, 2, 3, 4
NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError)


class Stop(Exception):
    def __init__(self, reason: str, halt: bool = False, **facts):
        super().__init__(reason)
        self.reason, self.halt, self.facts = reason, halt, facts


def _now_iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t, oplog.KYIV).isoformat(timespec="seconds")


def signal_key(signal: dict) -> str:
    digest = hashlib.sha256(json.dumps(signal, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    sid = re.sub(r"[^0-9A-Za-z]", "_", str(signal.get("id")))[:16]
    side = re.sub(r"[^a-z]", "_", str(signal.get("side")))[:8]
    return f"{sid}_{side}_{digest[:16]}"


def _positive(value, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise Stop("invalid_signal", field=name)
    return Decimal(str(value))


def parse_signal(signal, now: float, max_age_s: float = MAX_SIGNAL_AGE_S) -> dict:
    """Validate a memedesk signal against the format scanner.py writes."""
    if not isinstance(signal, dict):
        raise Stop("invalid_signal", field="$")
    sid, side = signal.get("id"), signal.get("side")
    if not isinstance(sid, str) or not re.fullmatch(r"\d{1,8}", sid):
        raise Stop("invalid_signal", field="id")
    if side not in ("buy", "sell"):
        raise Stop("invalid_signal", field="side")
    mint = signal.get("mint")
    try:
        if not isinstance(mint, str):
            raise ValueError
        pubkey(mint)
    except Exception:
        raise Stop("invalid_signal", field="mint")
    try:
        created = dt.datetime.fromisoformat(signal["created_at"])
        if created.tzinfo is None:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise Stop("invalid_signal", field="created_at")
    age = now - created.timestamp()
    if age < -FUTURE_SKEW_S:
        raise Stop("signal_from_future", age_s=round(age, 1))
    if age > max_age_s:
        raise Stop("signal_expired", age_s=round(age, 1), max_age_s=max_age_s)
    out = {"id": sid, "side": side, "mint": mint, "symbol": signal.get("symbol"), "created_at": signal["created_at"], "age_s": round(age, 1)}
    if side == "buy":
        out["size_sol"] = _positive(signal.get("size_sol"), "size_sol")
    else:
        out["token_amount_ui"] = _positive(signal.get("token_amount"), "token_amount")
        out["reason"] = signal.get("reason")
    return out


def map_intent(sig: dict) -> dict:
    """buy: SOL -> mint, amount = size_sol in lamports. sell: mint -> SOL, UI amount (decimals unknown)."""
    if sig["side"] == "buy":
        lamports = sig["size_sol"] * LAMPORTS_PER_SOL
        if lamports != lamports.to_integral_value():
            raise Stop("invalid_signal", field="size_sol")
        return {"input_mint": b58encode(WSOL), "output_mint": sig["mint"], "amount_raw": int(lamports), "amount_unit": "lamports",
                "slippage_bps": MAX_SLIPPAGE_BPS, "max_priority_fee_lamports": MAX_PRIORITY_FEE_LAMPORTS}
    return {"input_mint": sig["mint"], "output_mint": b58encode(WSOL), "amount_raw": None, "amount_unit": "token_ui",
            "token_amount_ui": str(sig["token_amount_ui"]), "slippage_bps": MAX_SLIPPAGE_BPS,
            "max_priority_fee_lamports": MAX_PRIORITY_FEE_LAMPORTS}


def check_supported(intent: dict) -> None:
    """Reuse the preflight's own intent limits (pair, amount cap, slippage, fee), then the build's fixed intent."""
    try:
        check_intent(Intent(pubkey(USER), pubkey(intent["input_mint"]), pubkey(intent["output_mint"]),
                            intent["amount_raw"] if intent["amount_raw"] is not None else 0,
                            intent["slippage_bps"], intent["max_priority_fee_lamports"]))
    except Reject as exc:
        if exc.reason == "amount_over_limit" and intent["amount_raw"] is None:
            raise Stop("sell_amount_unresolved")
        facts = {"cap_lamports": MAX_AMOUNT_LAMPORTS} if exc.reason == "amount_over_limit" else {}
        raise Stop(exc.reason, **facts)
    fixed = (build_mod.INPUT_MINT, build_mod.OUTPUT_MINT, build_mod.AMOUNT, build_mod.SLIPPAGE_BPS)
    if (intent["input_mint"], intent["output_mint"], intent["amount_raw"], intent["slippage_bps"]) != fixed:
        raise Stop("pipeline_intent_mismatch", build_amount=build_mod.AMOUNT)


def default_pipeline(out_dir: Path, intent: dict) -> tuple[int, dict]:
    from tools import live_once

    check_supported(intent)  # the build ignores the intent; refuse anything it would not build
    return live_once.run_once(out_dir)


class Bridge:
    def __init__(self, *, state_dir: Path = STATE_DIR, log_path: Path | None = None, pipeline=default_pipeline,
                 clock=time.time, fills_dir: Path | None = None, out_root: Path = OUT_ROOT,
                 max_age_s: float = MAX_SIGNAL_AGE_S, budget_s: float = DEFAULT_BUDGET_S):
        if not 0 < max_age_s <= MAX_SIGNAL_AGE_S:
            raise ValueError("max_age_s must be in (0, 600]")
        self.state_dir, self.log_path, self.pipeline, self.clock = Path(state_dir), log_path, pipeline, clock
        self.fills_dir, self.out_root, self.max_age_s, self.budget_s = fills_dir, Path(out_root), max_age_s, budget_s

    @property
    def kill_file(self) -> Path:
        return self.state_dir / "KILL"

    def _log(self, event: str, **data) -> None:
        oplog.append("note", {"component": "bridge", "event": event, **data}, data.get("message_sha256"), path=self.log_path)

    def _write(self, sub: str, name: str, payload: dict) -> Path:
        path = self.state_dir / sub / name
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        tmp.replace(path)
        return path

    def _claim(self, key: str, info: dict) -> bool:
        claims = self.state_dir / "claims"
        claims.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(claims / f"{key}.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w") as handle:
            json.dump({"status": "claimed", "claimed_at": _now_iso(self.clock()), **info}, handle)
        return True

    def _set_claim(self, key: str, status: str, **extra) -> None:
        path = self.state_dir / "claims" / f"{key}.json"
        current = json.loads(path.read_text(encoding="utf-8"))
        current.update({"status": status, "updated_at": _now_iso(self.clock()), **extra})
        self._write("claims", f"{key}.json", current)

    def _failed_fill(self, signal: dict, reason: str) -> str | None:
        if self.fills_dir is None or not isinstance(signal, dict) or signal.get("side") not in ("buy", "sell"):
            return None
        if not re.fullmatch(r"\d{1,8}", str(signal.get("id"))):
            return None
        path = Path(self.fills_dir) / f"{signal['id']}_{signal['side']}_fill.json"
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            return None
        with os.fdopen(fd, "w") as handle:
            json.dump({"status": "failed", "reason": f"bridge:{reason}"}, handle)
        return str(path)

    def _stop(self, key: str | None, signal, exc: Stop, claimed: bool) -> tuple[int, dict]:
        code = EXIT_HALTED if exc.halt else EXIT_STOPPED
        record = {"status": "halted" if exc.halt else "stopped", "reason": exc.reason, "signal_key": key,
                  "retry": False, "exit_code": code, **exc.facts}
        if exc.halt and not self.kill_file.exists():
            self.state_dir.mkdir(parents=True, exist_ok=True)
            self.kill_file.write_text(json.dumps({"tripped_at": _now_iso(self.clock()), "reason": exc.reason, "signal_key": key}), encoding="utf-8")
            record["kill_switch_tripped"] = True
        if claimed:
            fill = self._failed_fill(signal, exc.reason)
            if fill:
                record["failed_fill_written"] = fill
            self._set_claim(key, record["status"], reason=exc.reason)
        self._log("stop", **record)
        note = {"at": _now_iso(self.clock()), "signal": _brief(signal), **record}
        self._write("notifications", f"{int(self.clock() * 1000)}_{key or 'unkeyed'}.json", note)
        return code, record

    def process(self, signal) -> tuple[int, dict]:
        key = signal_key(signal) if isinstance(signal, dict) else None
        if self.kill_file.exists():
            # Halted before claiming: the signal is not consumed, but nothing runs while KILL exists.
            return self._stop(key, signal, Stop("kill_switch", halt=True, kill_file=str(self.kill_file)), claimed=False)
        if key is None:
            return self._stop(None, signal, Stop("invalid_signal", field="$"), claimed=False)
        self._log("received", signal_key=key, **_brief(signal))
        if not self._claim(key, {"signal": _brief(signal)}):
            record = {"status": "duplicate", "reason": "duplicate_signal", "signal_key": key, "retry": False, "exit_code": EXIT_DUPLICATE}
            self._log("duplicate", **record)
            return EXIT_DUPLICATE, record
        try:
            started = self.clock()
            sig = parse_signal(signal, started, self.max_age_s)
            intent = map_intent(sig)
            self._log("mapped", signal_key=key, intent=_public_intent(intent))
            check_supported(intent)
            out_dir = self.out_root / key
            self._set_claim(key, "pipeline_running")
            self._log("pipeline_start", signal_key=key, out_dir=str(out_dir))
            try:
                code, result = self.pipeline(out_dir, intent)
            except Stop:
                raise
            except NETWORK_ERRORS as exc:
                raise Stop("network_loss", halt=True, error_type=type(exc).__name__)
            except RuntimeError as exc:
                raise Stop("rpc_error", halt=True, error_type=type(exc).__name__)
            except Exception as exc:  # noqa: BLE001 - fail closed on anything unexpected
                raise Stop("pipeline_exception", halt=True, error_type=type(exc).__name__)
            elapsed = self.clock() - started
            if elapsed > self.budget_s:
                raise Stop("timeout", halt=True, elapsed_s=round(elapsed, 1), budget_s=self.budget_s)
            if self.kill_file.exists():
                raise Stop("kill_switch_during_run")
            if code != 0 or result.get("decision") != "approved":
                raise Stop(f"pipeline_denied:{result.get('reason')}", gate_reason=result.get("reason"))
            ticket = result.get("ticket") or {}
            expires = ticket.get("expires_at_epoch")
            if not isinstance(expires, (int, float)) or self.clock() >= expires:
                raise Stop("ticket_expired", message_sha256=ticket.get("message_sha256"))
        except Stop as exc:
            return self._stop(key, signal, exc, claimed=True)
        handoff = {
            "status": "ready_to_sign", "signal_key": key, "signal": _brief(signal), "intent": _public_intent(intent),
            "ticket": dict(ticket), "unsigned_b64_path": result.get("unsigned_b64_path"),
            "signer": USER, "sign_before": ticket.get("expires_at"),
            "next": "agent only: Phantom embedded-wallet signing of unsigned_b64_path before sign_before, then "
                    "tools/record_result.py SIGNATURE --ticket <ticket_path>; the bridge never signs or sends",
            "ticket_path": result.get("ticket_path"),
        }
        path = self._write("ready", f"{key}.json", handoff)
        self._set_claim(key, "ready_to_sign", ticket_id=ticket.get("ticket_id"))
        self._log("ready_to_sign", signal_key=key, ticket_id=ticket.get("ticket_id"), message_sha256=ticket.get("message_sha256"),
                  expires_at=ticket.get("expires_at"), handoff=str(path))
        return EXIT_READY, {"status": "ready_to_sign", "signal_key": key, "handoff": str(path), "ticket_id": ticket.get("ticket_id"),
                            "sign_before": ticket.get("expires_at"), "exit_code": EXIT_READY}


def _brief(signal) -> dict:
    if not isinstance(signal, dict):
        return {}
    keep = ("id", "side", "mint", "symbol", "size_sol", "token_amount", "reason", "created_at", "score")
    return {k: signal[k] for k in keep if k in signal and isinstance(signal[k], (str, int, float, bool, type(None)))}


def _public_intent(intent: dict) -> dict:
    return dict(intent)


USAGE = __doc__.split("\n\n")[1]


def main(argv: list[str], stdin=None) -> int:
    if "-h" in argv or "--help" in argv:
        print(USAGE)
        return EXIT_READY
    if not argv:
        print(USAGE, file=sys.stderr)
        return EXIT_USAGE

    def opt(name, cast=str, default=None):
        if name not in argv:
            return default
        index = argv.index(name)
        if index + 1 >= len(argv):
            raise ValueError(name)
        return cast(argv[index + 1])

    try:
        state_dir = opt("--state-dir", Path, STATE_DIR)
        kwargs = {"state_dir": state_dir, "log_path": opt("--log-path", Path), "fills_dir": opt("--fills-dir", Path),
                  "out_root": opt("--out", Path, OUT_ROOT), "max_age_s": opt("--max-age-s", float, MAX_SIGNAL_AGE_S),
                  "budget_s": opt("--budget-s", float, DEFAULT_BUDGET_S)}
        if ("--signal" in argv) == ("--stdin" in argv):
            raise ValueError("exactly one of --signal/--stdin")
        text = opt("--signal", Path).read_text(encoding="utf-8") if "--signal" in argv else (stdin or sys.stdin).read()
        bridge = Bridge(**kwargs)
    except (ValueError, OSError) as exc:
        print(f"usage error: {type(exc).__name__}", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return EXIT_USAGE
    try:
        signal = json.loads(text)
    except json.JSONDecodeError:
        signal = None
    code, record = bridge.process(signal)
    print(json.dumps(record, indent=2, default=str))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
