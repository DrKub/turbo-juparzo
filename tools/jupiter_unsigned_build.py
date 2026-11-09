"""Fresh UNSIGNED Jupiter build + on-chain evidence + read-only simulation.

Never signs, never sends. The only RPC methods it may call are in
READ_ONLY_METHODS; anything else raises. Transaction bytes and the request
document are written under OUT_DIR (default /tmp/jupiter-unsigned) and must
never be committed. Prints a JSON summary without transaction bytes.

Usage: python3 tools/jupiter_unsigned_build.py [--out DIR]
"""

import base64
import datetime as dt
import json
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from metis_offline.base58 import b58decode, b58encode, pubkey  # noqa: E402
from metis_offline.pda import associated_token_address  # noqa: E402
from metis_offline.preflight import ATA, MAX_AMOUNT_LAMPORTS, MAX_PRIORITY_FEE_LAMPORTS, MAX_SLIPPAGE_BPS, SYSTEM, TOKEN, USDC, WSOL  # noqa: E402
from metis_offline.wire import parse_transaction  # noqa: E402

USER = "8Xm6iFvk9J8rBi7BvRAbiZU2RAEE1b7AM714QmiEXQ8a"
INPUT_MINT = "So11111111111111111111111111111111111111112"
OUTPUT_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
AMOUNT = MAX_AMOUNT_LAMPORTS
SLIPPAGE_BPS = MAX_SLIPPAGE_BPS
JUP = "https://lite-api.jup.ag/swap/v1"
DEXES = "Meteora DLMM"
RPC = "https://api.mainnet-beta.solana.com"
KYIV = ZoneInfo("Europe/Kyiv")
READ_ONLY_METHODS = {"getAccountInfo", "getMultipleAccounts", "getBlockHeight", "isBlockhashValid", "simulateTransaction", "getMinimumBalanceForRentExemption"}


def now() -> str:
    return dt.datetime.now(KYIV).isoformat(timespec="seconds")


def http_json(url: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "User-Agent": "turbo-juparzo-preflight"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def rpc(method: str, params: list):
    if method not in READ_ONLY_METHODS:
        raise RuntimeError(f"refusing non-read-only RPC method {method}")
    out = http_json(RPC, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    if "error" in out:
        raise RuntimeError(f"{method}: {out['error']}")
    return out["result"]


def token_amount(account: dict | None) -> int | None:
    if not account:
        return None
    raw = base64.b64decode(account["data"][0])
    return int.from_bytes(raw[64:72], "little") if len(raw) >= 72 else None


def main(argv: list[str]) -> int:
    out_dir = Path(argv[argv.index("--out") + 1]) if "--out" in argv else Path("/tmp/jupiter-unsigned")
    out_dir.mkdir(parents=True, exist_ok=True)
    summary: dict = {"started_at": now()}

    query = {
        "inputMint": INPUT_MINT, "outputMint": OUTPUT_MINT, "amount": str(AMOUNT), "slippageBps": str(SLIPPAGE_BPS),
        "onlyDirectRoutes": "true", "instructionVersion": "V2", "swapMode": "ExactIn",
        # Restrict the venue to Meteora DLMM so the quote matches the preflight AMM allowlist.
        "dexes": DEXES,
    }
    quote = http_json(f"{JUP}/quote?{urllib.parse.urlencode(query)}")
    summary["quote"] = {
        "at": now(), "in": quote.get("inAmount"), "out": quote.get("outAmount"), "min_out": quote.get("otherAmountThreshold"),
        "slippage_bps": quote.get("slippageBps"), "price_impact_pct": quote.get("priceImpactPct"),
        "route": [{"label": s["swapInfo"].get("label"), "amm": s["swapInfo"].get("ammKey"), "in_mint": s["swapInfo"]["inputMint"],
                   "out_mint": s["swapInfo"]["outputMint"], "bps": s.get("bps"), "percent": s.get("percent")} for s in quote.get("routePlan", [])],
        "instruction_version": quote.get("instructionVersion"),
    }
    build = http_json(f"{JUP}/swap", {
        "quoteResponse": quote, "userPublicKey": USER, "wrapAndUnwrapSol": True, "dynamicComputeUnitLimit": True,
        "prioritizationFeeLamports": {"priorityLevelWithMaxLamports": {"maxLamports": MAX_PRIORITY_FEE_LAMPORTS, "priorityLevel": "medium"}},
    })
    wire_b64 = build["swapTransaction"]
    (out_dir / "unsigned.b64").write_text(wire_b64 + "\n", encoding="utf-8")
    raw = base64.b64decode(wire_b64)
    tx = parse_transaction(raw)
    last_valid = build["lastValidBlockHeight"]
    summary["build"] = {
        "at": now(), "last_valid_block_height": last_valid, "jupiter_reported_priority_lamports": build.get("prioritizationFeeLamports"),
        "jupiter_reported_cu_limit": build.get("computeUnitLimit"), "jupiter_simulation_error": build.get("simulationError"),
        "wire_len": len(raw), "blockhash": b58encode(tx.recent_blockhash),
    }

    tables = []
    for lookup in tx.lookups:
        info = rpc("getAccountInfo", [b58encode(lookup.table), {"encoding": "base64", "commitment": "confirmed"}])
        value = info["value"]
        if value is None:
            raise RuntimeError("lookup table account not found")
        tables.append({"address": b58encode(lookup.table), "owner": value["owner"], "data_b64": value["data"][0], "context_slot": info["context"]["slot"]})

    user = pubkey(USER)
    wsol_ata = b58encode(associated_token_address(user, WSOL, TOKEN, ATA))
    usdc_ata = b58encode(associated_token_address(user, USDC, TOKEN, ATA))
    watch = [USER, wsol_ata, usdc_ata]
    pre = rpc("getMultipleAccounts", [watch, {"encoding": "base64", "commitment": "confirmed"}])["value"]

    height = rpc("getBlockHeight", [{"commitment": "confirmed"}])
    request = {
        "transaction_b64": wire_b64,
        "intent": {"user": USER, "input_mint": INPUT_MINT, "output_mint": OUTPUT_MINT, "amount": AMOUNT,
                   "slippage_bps": SLIPPAGE_BPS, "max_priority_fee_lamports": MAX_PRIORITY_FEE_LAMPORTS},
        "lookup_tables": tables,
        "freshness": {"blockhash": b58encode(tx.recent_blockhash), "last_valid_block_height": last_valid, "current_block_height": height},
    }
    req_path = out_dir / "metis-request.json"
    req_path.write_text(json.dumps(request), encoding="utf-8")
    proc = subprocess.run([str(ROOT / "metis-offline"), str(req_path)], capture_output=True, text=True)
    summary["metis_offline"] = {"at": now(), "exit": proc.returncode, "report": json.loads(proc.stdout) if proc.stdout else None, "stderr": proc.stderr[-500:]}

    sim = rpc("simulateTransaction", [wire_b64, {
        "encoding": "base64", "sigVerify": False, "replaceRecentBlockhash": False, "commitment": "confirmed",
        "innerInstructions": True, "accounts": {"encoding": "base64", "addresses": watch},
    }])
    value = sim["value"]
    keys_static = [b58encode(k) for k in tx.account_keys]
    created = []
    inner_programs: dict = {}
    loaded = value.get("loadedAddresses") or {}
    all_keys = keys_static + (loaded.get("writable") or []) + (loaded.get("readonly") or [])
    for group in value.get("innerInstructions") or []:
        for ix in group.get("instructions", []):
            if "programIdIndex" in ix:
                prog = all_keys[ix["programIdIndex"]] if ix["programIdIndex"] < len(all_keys) else "?"
                accts = [all_keys[i] for i in ix.get("accounts", [])]
            else:
                prog = ix.get("programId", "?")
                accts = ix.get("accounts", [])
            inner_programs[prog] = inner_programs.get(prog, 0) + 1
            parsed = ix.get("parsed")
            if isinstance(parsed, dict):
                if parsed.get("type") in ("createAccount", "createAccountWithSeed"):
                    created.append(parsed.get("info", {}).get("newAccount"))
                continue
            data = b58decode(ix["data"]) if ix.get("data") else b""
            if prog == b58encode(SYSTEM) and data[:4] in (b"\0\0\0\0", b"\3\0\0\0") and len(accts) > 1:
                created.append(accts[1])
    post = value.get("accounts") or [None, None, None]
    pre_lamports = [a["lamports"] if a else 0 for a in pre]
    post_lamports = [a["lamports"] if a else 0 for a in post]
    usdc_pre, usdc_post = token_amount(pre[2]), token_amount(post[2])
    summary["simulation"] = {
        "at": now(), "slot": sim["context"]["slot"], "err": value.get("err"), "units_consumed": value.get("unitsConsumed"),
        "logs_tail": (value.get("logs") or [])[-4:], "inner_program_counts": inner_programs,
        "accounts_created_inside_tx": created,
        "wsol_ata": wsol_ata, "wsol_ata_exists_before": pre[1] is not None, "wsol_ata_exists_after": bool(post[1] and post[1]["lamports"]),
        "usdc_ata": usdc_ata, "usdc_ata_exists_before": pre[2] is not None,
        "usdc_raw_before": usdc_pre, "usdc_raw_after": usdc_post,
        "usdc_raw_delta": None if usdc_pre is None or usdc_post is None else usdc_post - usdc_pre,
        "user_lamports_delta": post_lamports[0] - pre_lamports[0],
        "persistent_accounts_created": [a for a in created if a != wsol_ata or bool(post[1] and post[1]["lamports"])],
    }
    end_height = rpc("getBlockHeight", [{"commitment": "confirmed"}])
    valid = rpc("isBlockhashValid", [b58encode(tx.recent_blockhash), {"commitment": "confirmed"}])["value"]
    summary["freshness_end"] = {"at": now(), "block_height": end_height, "last_valid_block_height": last_valid,
                                "blocks_left": last_valid - end_height, "isBlockhashValid": valid}
    summary["rent_165_bytes"] = rpc("getMinimumBalanceForRentExemption", [165])
    summary["finished_at"] = now()
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
