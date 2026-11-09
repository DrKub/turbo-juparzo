# Turnkey server-side signer (`tools/turnkey_signer.py`) — design and limits

Status: code + policies only. No Turnkey account, no API key, no request ever sent,
nothing signed or broadcast. `config/turnkey/signer.json` has `"enabled": false`.

## What the docs say (fetched 2026-11-09)

| Topic | Fact | Source |
|---|---|---|
| Stamping | Sign the exact JSON POST body with the API key (P-256: ECDSA/SHA-256, DER, hex); stamp JSON `{publicKey, signature, scheme: SIGNATURE_SCHEME_TK_API_P256}`, base64url, header `X-Stamp`. Doc vector for body `{"payload": "hello from TKHQ"}` verifies in our tests. | https://docs.turnkey.com/developer-reference/api-overview/stamps ; https://github.com/tkhq/sdk/blob/main/packages/api-key-stamper/src/index.ts |
| Sign | `POST https://api.turnkey.com/public/v1/submit/sign_transaction`, `type: ACTIVITY_TYPE_SIGN_TRANSACTION_V2`, `timestampMs`, `organizationId`, `parameters {signWith, unsignedTransaction, type: TRANSACTION_TYPE_SOLANA}`; result `activity.result.signTransactionResult.signedTransaction`. `@turnkey/solana` sends/receives hex. | https://docs.turnkey.com/api-reference/activities/sign-transaction ; https://github.com/tkhq/sdk/blob/main/packages/solana/src/index.ts |
| Activity status | COMPLETED / FAILED / CONSENSUS_NEEDED / PENDING; submissions idempotent by body fingerprint; `timestampMs` ≤ 1 h old, ≤ 5 min ahead. | https://docs.turnkey.com/api-reference/activities/overview |
| Broadcast | `SIGN_TRANSACTION` only signs ("ready to be broadcast"). Turnkey separately offers Transaction Management / "Broadcast SVM transaction" that broadcasts. We do NOT use it; the signer never broadcasts and has no RPC. | https://docs.turnkey.com/networks/solana |
| Policy evaluation | Root quorum bypasses policies; any DENY wins; else any ALLOW; else implicit deny. A condition with an evaluation error is skipped (erroring ALLOW allows nothing, erroring DENY denies nothing). `&&`/`||` don't short-circuit and have equal precedence. | https://docs.turnkey.com/concepts/policies/overview ; https://docs.turnkey.com/concepts/policies/language |
| Solana fields | `solana.tx.program_keys`, `instructions[].program_key / accounts[].account_key / instruction_data_hex`, `transfers[] {from,to,amount}` (top-level System transfers only), `spl_transfers[]` (top-level Transfer/TransferChecked only), `address_table_lookups`, `version`; ALT-resolved accounts appear as `ADDRESS_TABLE_LOOKUP`; program IDs from ALTs are rejected; `priority_fee_lamports` exists only for V1. | https://docs.turnkey.com/concepts/policies/language ; https://docs.turnkey.com/features/policies/examples/solana |
| Daily / cumulative | No stateful/aggregate field exists in the policy language (only per-request fields and time windows `time.now`, `CronSpan`). Spending windows exist only for swap fee sponsorship / fixed-rate swaps, not for signing. ⇒ daily limit is **local**. | same language page; https://docs.turnkey.com/_llms/documentation.txt |

## What enforces what

| Control | Turnkey policy (server) | Local (`turnkey_signer.py`) |
|---|---|---|
| Per-trade SOL cap 1 000 000 lamports | yes, via the top-level System transfer into the signer's WSOL ATA | yes (System transfers and Jupiter route_v2 in_amount) |
| Daily cap | **no** | yes, `data/turnkey-signer/daily.json` (Kyiv day; reserved before request, never refunded) |
| Allowed programs (ComputeBudget, System, Token, ATA, Jupiter) | yes (`program_keys.all`) | yes (metis-offline check before gate) |
| Swap output goes to own USDC ATA | yes (Jupiter accounts[2] pinned) | yes (metis-offline) |
| Withdrawals only to 3YxV | yes (separate ALLOW: System transfers only to 3YxV, no ALTs) | — (signer only accepts gate tickets for swaps) |
| Min output / slippage / priority fee (V0) | no | yes (metis-offline, sign gate) |
| Freshness, 45-block margin, single-use 20 s ticket | no | yes (sign gate + signer `used/` marker) |
| Kill switch | no | yes (`data/bridge/KILL`) |

Layout facts (verified offline on two real unsigned builds in /tmp from this repo): v0 with one ALT; all outer program IDs static; System transfer payer→WSOL ATA static; Jupiter accounts[0..2] = signer, WSOL ATA, USDC ATA static; mints and AMM via ALT. Policy conditions are NOT yet validated by Turnkey itself (no account) — validate with a non-root key before funding.

## Credential risk
The API key is a P-256 private key in env on this box. Whoever reads it can submit anything the policies allow: up to 1 000 000 lamports per transaction, **unlimited number of transactions** (no native daily cap), and withdrawals to 3YxV. The policies are the protection; the local daily cap does not stop a stolen key. Recommended: the API key belongs to a **non-root** user scoped by these policies; root quorum = the owner only (passkey), never on the box; fund the Turnkey wallet only with a small float.

## Integration plan
1. Owner creates the Turnkey org (root = owner passkey), a Solana wallet, a non-root API user + P-256 key.
2. Fill placeholders in `config/turnkey/policies.json` (user id, signer address, its WSOL/USDC ATAs), owner uploads them.
3. Parameterise the build/check for the Turnkey address (today `USER` = 8Xm6 is hardcoded, so every ticket is refused with `fee_payer_mismatch`).
4. Fund a small float; set env; flip `enabled`; choose transport `https`.
5. Broadcast remains unimplemented; a separate, explicitly approved step would add it.
