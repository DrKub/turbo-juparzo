"""One-shot, no-pause pipeline: build -> check -> simulate -> gate. Never signs or sends.

usage: python3 tools/live_once.py [--out DIR] [--wallet PROFILE]

Runs tools/jupiter_unsigned_build.py (fresh quote/build, lookup tables,
./metis-offline, read-only simulation; logs build/check/simulate) and then
immediately tools/sign_gate.py on the same directory (logs the gate decision).
On approval it prints the unsigned base64 path and the ticket; the external
signer must be invoked right away, before ticket.expires_at, and the signature
recorded with tools/record_result.py --ticket. Exit 0 approved, 2 denied.
There are no keys here and no send RPC: sendTransaction stays refused by
tools/readonly_rpc.py.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import jupiter_unsigned_build, sign_gate  # noqa: E402


def run_once(out_dir: Path, *, build=None, gate=None, wallet_profile: str | None = None) -> tuple[int, dict]:
    """Build -> check -> simulate -> gate on OUT_DIR. Returns (exit code, result). Never signs or sends.

    Exceptions from the build (network loss, RPC errors) propagate to the caller.
    """
    import contextlib
    import io

    from tools import wallet

    profile = wallet.load_profile(wallet_profile)
    build = build or jupiter_unsigned_build.main
    gate = gate or (lambda d: sign_gate.run(d, expected_user=profile.address))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        build_code = build(["--out", str(out_dir), "--wallet", profile.name])
    if build_code != 0:
        return 2, {"decision": "denied", "reason": "build_failed"}
    code, result = gate(out_dir)
    out = {"decision": result["decision"], "reason": result["reason"], "wallet_profile": profile.name, "user": profile.address}
    if code == 0:
        out["unsigned_b64_path"] = str(out_dir / "unsigned.b64")
        out["ticket"] = result["ticket"]
        out["ticket_path"] = str(sign_gate.STATE_DIR / "tickets" / f"{result['ticket']['ticket_id']}.json")
    else:
        out.update({k: v for k, v in result.items() if k not in out})
    return code, out


def main(argv: list[str]) -> int:
    out_dir = Path(argv[argv.index("--out") + 1]) if "--out" in argv else Path("/tmp/jupiter-live-once")
    code, out = run_once(out_dir, wallet_profile=argv[argv.index("--wallet") + 1] if "--wallet" in argv else None)
    print(json.dumps(out, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
