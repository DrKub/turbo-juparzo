# Scanner -> turbo-juparzo bridge (`tools/bridge.py`)

Never signs, never sends. The furthest it goes is a `ready_to_sign` handoff
file with the sign-gate ticket in `data/bridge/ready/<key>.json`.

## Input
One memedesk signal, as written by `/workspace/memedesk/scanner.py`:

- buy `live/signals/NNNN_buy.json`: `id`, `side:"buy"`, `mint`, `symbol`, `name`, `pair`,
  `dexscreener_url`, `price_usd`, `size_sol` (UI SOL, floor4 of 8 % of free cash), `mcap`,
  `liquidity`, `volume_1h`, `age_hours`, `top10_pct`, `score`, `created_at` (ISO, Kyiv offset);
- sell `live/signals/NNNN_sell.json`: `id` (same id as the buy), `side:"sell"`, `mint`,
  `symbol`, `reason` (`stop_loss` / `trailing_stop` / `time_stop`), `trigger_price`,
  `token_amount` (UI amount, whole position), `created_at`.

`python3 tools/bridge.py --signal FILE` or `--stdin`. Signals older than 600 s
(the scanner's fill timeout) or from the future are refused.

## Mapping
buy -> SOL to `mint`, amount = `size_sol` in lamports. sell -> `mint` to SOL,
UI amount (decimals not resolved). The intent is then checked with the
preflight's own `check_intent` (pair WSOL->USDC only, <= 1 000 000 lamports,
<= 50 bps, priority fee <= 10 000) and against the build's fixed intent
(`tools/jupiter_unsigned_build.py` takes no parameters). Only then does it call
`tools/live_once.run_once` (build -> metis-offline -> simulation -> sign gate).

Consequence today: every real memedesk signal stops at `unsupported_pair`
before any network call. Only a synthetic 0.001 SOL -> USDC signal can reach
the pipeline.

## Stop rule
Single use per signal: `data/bridge/claims/<id>_<side>_<sha16>.json` (O_EXCL).
Identical signal -> exit 4. New signal for the same coin -> new key, allowed.
Any failure -> claim `stopped`/`halted`, `note` entry `event:"stop"` in
`logs/operations.jsonl`, `data/bridge/notifications/*.json`, no retry.
Network loss, RPC error, unexpected exception, over-budget (default 90 s)
-> also writes the kill switch `data/bridge/KILL`; while it exists every call
exits 3 without claiming. Only a human removes it. `--fills-dir` optionally
writes `{"status":"failed"}` fills for the scanner (never overwrites, never
"filled").

Exit codes: 0 ready_to_sign, 1 usage, 2 stopped, 3 halted, 4 duplicate.
