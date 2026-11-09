"""Read-only Solana RPC client. Any method outside READ_ONLY_METHODS is refused.

sendTransaction, sendRawTransaction and requestAirdrop are deliberately absent.
"""

import json
import urllib.request

RPC = "https://api.mainnet-beta.solana.com"
READ_ONLY_METHODS = frozenset({
    "getAccountInfo",
    "getMultipleAccounts",
    "getBlockHeight",
    "isBlockhashValid",
    "simulateTransaction",
    "getMinimumBalanceForRentExemption",
    "getTransaction",
    "getSignatureStatuses",
})


def http_json(url: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "User-Agent": "turbo-juparzo-preflight"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def rpc(method: str, params: list, url: str = RPC):
    if method not in READ_ONLY_METHODS:
        raise RuntimeError(f"refusing non-read-only RPC method {method}")
    out = http_json(url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    if "error" in out:
        raise RuntimeError(f"{method}: {out['error']}")
    return out["result"]
