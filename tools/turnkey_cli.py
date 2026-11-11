"""Turnkey helper commands. Nothing here runs by itself; every command is explicit.

  python3 tools/turnkey_cli.py pubkey [--key PEM]      print the API key's PUBLIC key (compressed hex). Offline, read-only.
  python3 tools/turnkey_cli.py check  [--key PEM]      ONE read-only Turnkey call (list_wallets) with that key. Network.
  python3 tools/turnkey_cli.py sign   [--out DIR]      build+check+simulate+gate for the turnkey wallet, then ONE Turnkey
                                                       sign request. Needs signer.json "enabled": true. Never broadcasts.
  python3 tools/turnkey_cli.py broadcast --signed FILE --ticket FILE [--broadcast-confirm]
                                                       send a signed swap. Needs signer.json "broadcast": true AND the flag.

Key source for check/sign: --key / env TURNKEY_API_KEY_PATH / config "api_key_path" (default the box key
/home/box/.config/turnkey/api_private.pem). The private key is never printed or logged.
Exit codes: 0 ok, 1 usage/config, 2 refused or denied, 3 halted (network/transport).
"""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import turnkey_broadcast, turnkey_signer as ts, wallet  # noqa: E402
from tools.bridge import Stop  # noqa: E402

LIST_WALLETS_PATH = "/public/v1/query/list_wallets"
DEFAULT_KEY = "/home/box/.config/turnkey/api_private.pem"


def _opt(argv, name, default=None):
    if name not in argv:
        return default
    i = argv.index(name)
    if i + 1 >= len(argv):
        raise ValueError(name)
    return argv[i + 1]


def _key_path(argv, cfg) -> str:
    import os
    return _opt(argv, "--key") or os.environ.get("TURNKEY_API_KEY_PATH") or cfg.get("api_key_path") or DEFAULT_KEY


def cmd_pubkey(argv, out=print) -> int:
    cfg = ts.load_config()
    out(ts.public_key_hex(Path(_key_path(argv, cfg)).expanduser()))
    return 0


def cmd_check(argv, transport=ts.urllib_transport, clock=time.time, out=print) -> int:
    cfg = ts.load_config()
    env = {"TURNKEY_API_KEY_PATH": _key_path(argv, cfg)}
    creds = ts.Credentials.resolve(cfg, env)
    body = json.dumps({"organizationId": creds.organization_id})
    stamp = ts.p256_stamp(body, creds.api_public_key, creds._private)
    url = cfg.get("api_base", "https://api.turnkey.com").rstrip("/") + LIST_WALLETS_PATH
    status, payload = transport(url, body, {"Content-Type": "application/json", "X-Stamp": stamp, "User-Agent": ts.USER_AGENT},
                                float(cfg.get("request_timeout_s", 10)))
    if status == 200 and isinstance(payload, dict) and isinstance(payload.get("wallets"), list):
        out(json.dumps({"status": "ok", "api_public_key": creds.api_public_key,
                        "wallets": [{"name": w.get("walletName"), "id": w.get("walletId")} for w in payload["wallets"]]}, indent=2))
        return 0
    hint = "key_not_registered_in_turnkey_org" if status == 401 else None
    out(json.dumps({"status": "failed", "http": status, "hint": hint, "api_public_key": creds.api_public_key,
                    "message": str((payload or {}).get("message", payload))[:300]}, indent=2))
    return 2


def cmd_sign(argv, run_once=None, signer_factory=None, out=print) -> int:
    from tools import live_once

    run_once = run_once or live_once.run_once
    try:
        signer = signer_factory() if signer_factory else ts.TurnkeySigner.from_environment()
    except ts.ConfigError as exc:
        out(json.dumps({"status": "refused", "reason": f"signer_unavailable:{exc}"}))
        return 1
    profile = wallet.load_profile("turnkey")
    code, result = run_once(Path(_opt(argv, "--out", "/tmp/turnkey-sign")), wallet_profile="turnkey")
    if code != 0 or result.get("decision") != "approved":
        out(json.dumps({"status": "denied_before_signing", "reason": result.get("reason")}, indent=2))
        return 2
    handoff = {"status": "ready_to_sign", "ticket": result["ticket"], "unsigned_b64_path": result["unsigned_b64_path"],
               "wallet_profile": "turnkey", "signer": profile.address, "ticket_path": result.get("ticket_path")}
    try:
        signed = signer(handoff)
    except Stop as exc:
        out(json.dumps({"status": "stopped", "reason": exc.reason, **{k: v for k, v in exc.facts.items()}}, indent=2, default=str))
        return 3 if exc.halt else 2
    out(json.dumps({**signed, "ticket_path": handoff["ticket_path"], "broadcast": False}, indent=2))
    return 0


def cmd_broadcast(argv, send=None, out=print, **kwargs) -> int:
    try:
        signed, ticket = Path(_opt(argv, "--signed")), Path(_opt(argv, "--ticket"))
    except (ValueError, TypeError):
        out("usage: broadcast --signed FILE --ticket FILE [--broadcast-confirm]")
        return 1
    extra = {"send": send} if send else {}
    try:
        result = turnkey_broadcast.broadcast(signed, ticket, confirm="--broadcast-confirm" in argv, **extra, **kwargs)
    except turnkey_broadcast.BroadcastRefused as exc:
        out(json.dumps({"status": "refused", "reason": exc.reason, **exc.facts}, indent=2))
        return 2
    except Exception as exc:  # noqa: BLE001 - send attempted or state unknown: never retry
        out(json.dumps({"status": "send_error_no_retry", "error_type": type(exc).__name__}, indent=2))
        return 3
    out(json.dumps(result, indent=2))
    return 0


def main(argv: list[str], **kwargs) -> int:
    commands = {"pubkey": cmd_pubkey, "check": cmd_check, "sign": cmd_sign, "broadcast": cmd_broadcast}
    if not argv or argv[0] not in commands:
        print(__doc__, file=sys.stderr)
        return 1
    try:
        return commands[argv[0]](argv[1:], **kwargs)
    except (ts.ConfigError, ValueError, wallet.WalletConfigError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
