"""One-shot, no-pause pipeline: build -> check -> simulate -> gate. Never signs or sends.

usage: python3 tools/live_once.py [--out DIR]

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


def main(argv: list[str]) -> int:
    out_dir = Path(argv[argv.index("--out") + 1]) if "--out" in argv else Path("/tmp/jupiter-live-once")
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        build_code = jupiter_unsigned_build.main(["--out", str(out_dir)])
    if build_code != 0:
        print(json.dumps({"decision": "denied", "reason": "build_failed"}))
        return 2
    code, result = sign_gate.run(out_dir)
    out = {"decision": result["decision"], "reason": result["reason"]}
    if code == 0:
        out["unsigned_b64_path"] = str(out_dir / "unsigned.b64")
        out["ticket"] = result["ticket"]
        out["ticket_path"] = str(sign_gate.STATE_DIR / "tickets" / f"{result['ticket']['ticket_id']}.json")
    else:
        out.update({k: v for k, v in result.items() if k not in out})
    print(json.dumps(out, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
