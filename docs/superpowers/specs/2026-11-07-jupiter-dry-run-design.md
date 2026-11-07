# Jupiter dry-run executor

Status: design approved for a written spec on 2026-11-07. Approval recorded for this phase: «Утверждаю проект: сначала тестовый режим без ключей и реальных сделок».

This file is the spec for the dry-run phase only. It does not authorize implementation, an implementation plan, a live flag, a renewed trading mandate, deployment, or a process restart. The next step is user review of this file.

The previous live mandate expired at 2026-11-07 08:00 Europe/Kiev (`2026-11-07T08:00:00+02:00`). Expiry is final. No environment variable, CLI flag, config file, later expiry, or default can sign, submit, or revive that mandate. This phase accepts no replacement mandate.

## Repository

This repository is the Jupiter work (`turbo-juparzo`). At the time of this spec the committed tree is the existing README plus this document. This change adds only this document. It does not add runtime files, a package, or a `.gitignore`.

The memedesk scanner is an external process on the parent machine at `/workspace/memedesk`. It is not in this VM and it is not assumed to be in this repository. This spec does not copy scanner source, and it does not invent scanner filenames, webhook URLs, webhook credentials, or RPC URLs. Those artifacts can be specified only after real samples are provided.

## External scanner contract

The executor owns one inbox directory of immutable JSON files. A later integration may copy scanner signals into that inbox. This phase's tests point the inbox at fixture copies. The executor opens inbox files read-only. It does not rename, delete, or mark them handled, and it does not write them back to the scanner.

Dry-run receipts use `"status": "dry_run"` and live only under the executor directory defined below. They are not scanner fills. Nothing in this phase writes a scanner `live/` or `fills/` path, and nothing marks a live scanner signal handled.

Journal dedup is not a fix for the scanner. A previous `SIGTERM` restart of that external scanner produced a spurious liquidation. A future integration must stop that scanner in an orderly way that generates no trade. This dry-run process, including on `SIGTERM`, writes no sell signal and no liquidation record.

## Phase result

One single-threaded process. No language model on the signal path. No worker pool.

1. Refuse live or signing configuration before any lock, read, or network call.
2. Take one exclusive OS lock for the rest of the process lifetime.
3. Rebuild the simulated book by folding the durable journal.
4. Read the inbox copy, validate it, and apply pre-quote risk checks.
5. Request a Jupiter quote with no `taker` and no API key.
6. Commit a dry-run receipt, or leave a retryable quote miss uncommitted.
7. For each position still open, take at most one mark quote and apply stop and trailing rules.

There is no send path and no sign path. The signer is a refusal function. Calling it fails. It does not load a key, read a Phantom session, create a wallet, or return signature bytes. The quote path does not call it.

## Startup refusal

Exit code 3, before the lock, if any of the following is true:

- A CLI flag or config value requests live execution, including `--live`, `MODE=live`, `LIVE=1`, or `DRY_RUN` set to `0` or `false`.
- A mandate file or mandate expiry is present, including an expiry later than 2026-11-07 08:00 Europe/Kiev.
- Configuration supplies a taker, a fee payer, a referral account, an API key, an `/execute` URL, a `/build` URL, a signer, a wallet secret, an RPC URL, or a webhook URL.
- The dry-run config contains a key outside the closed set below, or a configured denylist entry is not a valid mint.

The process does not ignore `MODE` and continue. There is no dormant live branch behind the refusal.

## Modules

A later implementation, after a separate approval, would add a new package. It would not import scanner code that is not in this repository.

| Module | Responsibility |
| --- | --- |
| `executor` | One process. Orders the steps. Refuses live configuration. |
| `lock` | Non-blocking exclusive OS file lock. The kernel releases it when the process exits. |
| `journal` | Durable record per `(id, side)`, plus durable mark observations. `fsync`, then atomic rename. |
| `schema` | Accepts or rejects one inbox file before any quote. |
| `risk` | Bank, reserve, position count, one open position per mint, denylist, sell size, stop, and trailing. |
| `quote` | `GET /swap/v2/order` with no `taker`. No other Jupiter route. |
| `signer` | Refusal function. Any call fails. The quote path does not call it. |
| `receipt` | Writes only the dry-run receipt schema, in the dry-run directory. |

## Local config and fixtures

The closed config keys are `bank_lamports`, `mint_metadata`, `denylist_mints`, and `buy_estimates`.

- `bank_lamports` is a canonical positive integer string. It is the simulated starting SOL balance, in lamports. The process does not read a chain balance.
- `mint_metadata` is a list of `{mint, decimals}`. `decimals` is an integer from 0 through 255 supplied by the fixture. A missing mint, a missing decimals field, or a non-integer is `unknown_mint`. The process does not guess 6, 9, or any other default.
- `denylist_mints`, when present, is a list of mint addresses. See ECR below.
- `buy_estimates` maps a buy signal `id` to `estimated_fee_lamports` and `estimated_rent_lamports`, each a canonical non-negative integer string. These are local dry-run inputs, not scanner fields. A buy with no estimate is rejected. The process does not invent a fee or embed a rent constant.

Fixture numbers are examples for offline tests. They are not a claim about real balances or trade profitability. No fixture, log, or committed file may contain a secret, an API key, a `.env` value, a Helius or other RPC URL, or a webhook URL or credential.

The public agent wallet `8Xm6iFvk9J8rBi7BvRAbiZU2RAEE1b7AM714QmiEXQ8a` is context only. This phase does not hardcode it, does not send it as `taker`, and does not sign for it. A whitelisted withdrawal exists outside this phase and is out of scope: this process has no withdrawal function and no withdrawal whitelist.

## Inbox signal

Required fields:

| Field | Rule |
| --- | --- |
| `id` | One path segment matching `^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$`, and not starting with `rexit.`. The journal key is `(id, side)`, not `id` alone. |
| `side` | `buy` or `sell`. |
| `mint` | Base58 string that decodes to 32 bytes. A symbol is not a mint. |
| `created_at` | ISO-8601 timestamp with an offset. Compared as an instant. |

Buy signals also require `size_sol` and forbid `token_amount`. Sell signals also require `token_amount` and forbid `size_sol`. Any other field rejects that signal with reason `schema`. The raw body is not copied into the journal or the log. A schema log line contains the reason and the `id` only when the id matches the pattern above.

`size_sol` is a positive plain decimal string with no exponent. Conversion to lamports multiplies by `10^9` and requires an exact integer. Values that need rounding are rejected. Zero and negative amounts are rejected.

`token_amount` is a positive canonical integer string of base units: digits only, with no leading zero. This phase does not convert a UI amount.

The SOL side of every quote is the mint Jupiter documents for SOL, `So11111111111111111111111111111111111111112`. A signal whose `mint` is that address is rejected with reason `pair`. Buys quote SOL to the signal mint. Sells quote the signal mint to SOL. No other pair is quoted. The signal set does not create an allowlist.

Processing order in one invocation is `created_at`, then `id`, then `side` with `buy` before `sell`. A record committed earlier in that same invocation is already in the fold when the next signal is checked.

## Simulated book

All amounts used for decisions are arbitrary-precision integers. IEEE floats are not a decision input.

The book is the fold of the starting `bank_lamports` and the committed journal. It is not a second source of truth. A cache file, if a later implementation writes one, is rebuilt from the journal under the lock and is ignored when it disagrees.

A committed quoted buy subtracts `buy_lamports + estimated_fee_lamports + estimated_rent_lamports` from the simulated bank and opens one position. A committed quoted sell adds the quote `outAmount` (SOL lamports) to the simulated bank and closes that position. Jupiter's platform fee stays inside the quoted amounts; the fold does not subtract it again. Rejected receipts change neither the bank nor the positions.

An open position stores `entry_buy_lamports`, `entry_out_base_units`, and the best mark seen since entry. Entry price uses the buy principal and the tokens quoted out; fee and rent do not enter it. The best mark is the pair `(mark_out_lamports, mark_in_base_units)` with the greatest price, compared by cross-multiplication. Until a mark is committed, the entry pair is the high-water mark. Armed is true when a committed mark has met the 12 percent inequality below. Both are recomputed from the journal.

## Pre-quote risk checks

These checks use the folded book and the local config. They do not use a quote.

Let `buy_lamports` be the exact lamport value of `size_sol`, and let `fee` and `rent` be the local estimates.

- Cap: reject reason `risk_cap` when `buy_lamports * 100 > bank_lamports * 8`. Exactly 8 percent passes. `bank_lamports` here is the current simulated bank after the fold, not a frozen copy of the starting value.
- Reserve: `post = bank_lamports - buy_lamports - fee - rent`. Reject reason `risk_reserve` when `post < 30000000`. Exactly `30000000` lamports (0.03 SOL) passes. Fee and rent are subtracted once, inside that formula, and the 0.03 SOL is what must remain afterward. They are not subtracted a second time, and they are not left out of `post`.
- Positions: at most three open positions. A fourth open is reason `risk_max_positions`.
- One mint: a buy of a mint that already has an open position is reason `risk_mint_open`. After a committed full close, a later buy may open that mint again. Two overlapping opens are forbidden.
- Sells: reason `risk_no_position` when that mint is not open. Reason `risk_sell_size` when `token_amount` is not exactly the open base-unit balance. This phase does not clip and does not partially close. A quoted signal sell closes that one position.
- Denylist and unknown mint run before the quote, as specified below.

Fictional check, not a balance claim: bank `1000000000`, buy `80000000`, fee `5000`, rent `0` passes both gates (`post = 919995000`). Buy `80000001` fails the cap. Bank `40000000`, buy `3200000`, fee `7000000`, rent `0` passes the cap and fails the reserve (`post = 29800000`). The same buy with fee `6800000` leaves `post = 30000000` and passes.

## Stop and trailing

Stop and trailing run only after inbox signals have been applied, and only for positions still open. Each such position gets at most one mark quote per invocation: a quote-only sell of the full open base-unit balance back to SOL.

Let entry be `(entry_buy_lamports, entry_out_base_units)` and the mark be `(mark_out_lamports, mark_in_base_units)`.

On one mark, in this order:

1. Stop when `mark_out_lamports * entry_out_base_units * 100 <= entry_buy_lamports * mark_in_base_units * 94`. That is a loss of 6 percent or worse against entry. Equality fires.
2. Otherwise, if already armed before this mark, giveback fires when `mark_out_lamports * high_in_base_units * 100 <= high_out_lamports * mark_in_base_units * 94`. That is 6 percent under the stored high. Equality fires and writes one exit with reason `trailing`. Giveback does not use the current mark as its own high, and it does not run before arming.
3. Otherwise commit the mark. If its price is strictly better than the stored high, it becomes the high. It also arms when `mark_out_lamports * entry_out_base_units * 100 >= entry_buy_lamports * mark_in_base_units * 112`. Equality arms. A mark cannot arm and give back in the same step.

If the stop inequality holds, the exit reason is `stop` and giveback is not written. The exit is a quoted dry-run sell of the full open balance. Its id is `rexit.<buy_id>.stop` or `rexit.<buy_id>.trailing`, and its side is `sell`. A committed exit is not sent again. An exit decision that was not renamed is forgotten; the next wake quotes again and may see a different price.

A mark that does not exit is durable, so a crash does not forget the high-water mark. A failed or `quote_unsafe` mark stores no bytes and commits nothing; the position stays open and a later invocation may mark it again. A `quote_unsafe` signal, by contrast, is a terminal receipt for that `(id, side)`.

Fictional price path, not a result: entry price 1.00 stops at or below 0.94, arms at or above 1.12, and a peak of 1.20 gives back at or below 1.128. A later mark of 1.10 with no earlier arm does not trail-exit.

## ECR denylist

ECR is blocked only by an explicit verified mint address on `denylist_mints`. A ticker or symbol, including the string `ECR`, is not a block. This repository does not contain a verified ECR mint, and this spec does not invent one.

If `denylist_mints` is absent, dry-run may quote and every receipt records `"denylist": "not_configured"`. If the key is present, receipts record `"denylist": "configured"`, including when the list is empty. Both the absent key and a list that does not contain the verified ECR mint block a future live start. Neither rejects other fixture mints. A signal mint that exactly equals a configured entry is rejected with reason `denylist` before quote. Missing mint identity remains a live-start blocker. An absent `buy_estimates` key is an empty map, not a startup failure. Missing or invalid `bank_lamports` is exit 3.

## Quote adapter

The only network call this design allows is:

`GET https://api.jup.ag/swap/v2/order`

The query string has only `inputMint`, `outputMint`, and `amount`. `amount` is the integer base units from schema validation. The request omits `taker`, so the documented contract returns a quote and no transaction (`transaction` null). The request also omits `receiver`, `payer`, `referralAccount`, `referralFee`, `slippageBps`, and the `x-api-key` header.

Checked against Jupiter's docs on 2026-11-07:

- [Swap API](https://developers.jup.ag/docs/swap): without `taker`, `/order` returns the quote and `transaction: null`. The documented keyless example is SOL (`So11111111111111111111111111111111111111112`) to USDC (`EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v`) with `amount=100000000`. Keyless traffic is limited to 0.5 requests per second.
- [Order and Execute](https://developers.jup.ag/docs/swap/order-and-execute): the signing example sends `taker` and then `POST /execute`. This phase does not follow that example past the quote.
- [Get Order](https://developers.jup.ag/docs/api-reference/swap/order): `inputMint`, `outputMint`, and `amount` are required. `taker` is optional and, when absent, the response has a quote and no transaction. `transaction` is null without `taker`, and an empty string only when `taker` was sent and the transaction could not be built.
- [Execute](https://developers.jup.ag/docs/api-reference/swap/execute): `POST /swap/v2/execute` requires `signedTransaction` and `requestId`. This phase never calls it.
- [Rate limits](https://developers.jup.ag/docs/portal/rate-limits): keyless tier is 0.5 requests per second and 30 requests per minute on a 60-second sliding window. `/execute` has a separate bucket and is still forbidden here.

A quote is successful only when the HTTP status is 200, `transaction` is JSON null, `inAmount` equals the requested amount, and `outAmount` is a positive canonical integer string. Any present transaction string, including `""`, is `quote_unsafe`: the body is discarded, bytes are not stored, and the signer is not called. On an inbox signal, `quote_unsafe` is a terminal rejected receipt and is not retried. On a mark, `quote_unsafe` commits nothing. A non-200 status, a `429`, a timeout, a mint echo mismatch, or a missing amount also commits nothing, for both signals and marks. The next invocation may make one new read-only attempt for a key that has no terminal record. That retry is not exactly-once delivery, and it is safe only because nothing is signed or submitted. One invocation does not loop on failure.

The stored quote fields are `router`, `inAmount`, `outAmount`, `feeBps`, `feeMint`, and `platformFee` when present. `transaction`, `requestId`, route plans, and USD values are not stored. USD fields are not a performance result.

Jupiter's published `/order` platform fee, as of the same docs, is 0 bps for Jupiter-token buys and pegged-asset swaps, 2 bps for SOL to stablecoin, 5 bps for liquid staking token to stablecoin, 10 bps otherwise, and 50 bps for tokens younger than 24 hours. The response `feeBps` explains the quote. This phase does not reimplement that table. Referral fees are not used. Ultra and Swap API v1 are not called.

Default tests use a fake transport and zero network. The optional smoke command is outside the default suite and outside CI. It performs one keyless `GET /order` without `taker` for the documented SOL to USDC example, checks the success shape above, prints `router`, `inAmount`, `outAmount`, and `feeBps`, and writes no receipt. It does not assert a particular `outAmount`. A rate limit or network error fails that command only.

## Journal, lock, and crash recovery

The lock file is `data/jupiter-dry-runs/executor.lock`. The lock is `flock` or an equivalent non-blocking exclusive OS advisory lock. A second process fails to acquire it, exits code 2, and does not read the inbox, quote, or write. A crashed process does not leave a lock that an operator must delete.

The journal file is the receipt. There is no second receipt copy.

- `data/jupiter-dry-runs/journal/<id>/<side>.json`

Mark observations that do not exit:

- `data/jupiter-dry-runs/journal/<buy_id>/marks/<n>.json`

`<buy_id>` is the opening buy id. `<n>` is one plus the count of mark files already committed for that buy. The write is a temporary file in the same directory, `fsync`, then rename over the final name. States committed by this phase are the dry-run receipt and the mark observation. There is no `signed`, `submitted`, `filled`, or `ambiguous` state.

Recovery, after the lock is held:

- A renamed terminal record for the same `(id, side)` is reused. The process does not quote again and does not write another receipt. The book fold includes it once.
- A temporary file with no renamed record is discarded. Recovery may then make one read-only quote for that key. A repeated `GET /order` has no chain effect.
- The same `(id, side)` from two inbox files produces one terminal record. The same id with the other side is a different key.
- A renamed mark file remains part of the high-water fold. It is not a sell.

`data/jupiter-dry-runs/` is runtime state. The implementation change that creates it must gitignore it. This documentation change writes none of those files. Receipts must not be written anywhere else.

## Receipts

```json
{
  "record_type": "jupiter_dry_run_receipt",
  "id": "example-buy-1",
  "side": "buy",
  "status": "dry_run",
  "outcome": "quoted",
  "trigger": "signal",
  "mint": "ExampleMintNotAnAllowlist",
  "quoted_in_amount": "1000000",
  "quoted_out_amount": "1",
  "reason": null,
  "denylist": "not_configured"
}
```

The amounts and mint above are fictional fixture values, not a quote and not an allowlist entry. `status` is always `dry_run`. `outcome` is `quoted` or `rejected`. `trigger` is `signal`, `stop`, or `trailing`. `reason` is null on a quote, otherwise one of `schema`, `risk_cap`, `risk_reserve`, `risk_max_positions`, `risk_mint_open`, `risk_no_position`, `risk_sell_size`, `unknown_mint`, `pair`, `denylist`, or `quote_unsafe`.

The receipt has no `signature` and no live fill fields. `status` is never `filled`, `signed`, or `submitted`. Writing a receipt does not update a scanner position and does not mark a source signal handled. Rejected receipts use null quote amounts.

## Tests that belong to a later implementation

The default suite is offline and deterministic. It uses fixtures and the fake transport. It must cover:

- Startup refusal exits 3, without the lock or a quote, for `--live`, `MODE=live`, a mandate file, a taker, an API key, and an `/execute` URL.
- A second process exits 2 and does not quote or write.
- The same `(id, side)` yields one journal file, which is the only receipt, including across a restart. The same id with the other side is a different record.
- A crash before rename may perform one read-only quote on recovery. A crash after rename does not quote again. A committed mark still affects the high-water fold after restart.
- Buys over 8 percent, buys that leave fewer than `30000000` lamports after the stated fee and rent, buys with a missing fee or rent estimate, and zero amounts are rejected with no quote. A buy of exactly 8 percent that still leaves at least `30000000` lamports reaches the quote step.
- A fourth open position is rejected. A second buy of a mint that is already open is rejected. A sell with no open position, or with a `token_amount` other than the open base-unit balance, is rejected. A quoted sell closes only that position. After that close, a later buy of the same mint may open a new position.
- A mark at a 6 percent loss commits one `stop` exit and no mark file. A mark at a 12 percent gain commits a mark, arms, and does not exit. A later mark 6 percent under that stored peak commits one `trailing` exit. A second wake does not emit another exit. Stop wins when both inequalities hold.
- An exact denylist mint is rejected. The string `ECR` is not. An absent denylist records `not_configured`. A present empty denylist records `configured`. Neither rejects other fixture mints, and both block a future live start until the verified ECR mint is known.
- A mint with no supplied decimals is rejected. No test passes because the code assumed 6 or 9 decimals.
- The quote request has no `taker`, no API key, and no path other than `/swap/v2/order`. Transaction bytes in a fixture response are not stored. On a signal they produce a terminal `quote_unsafe` receipt. On a mark they commit nothing. A transport failure commits no terminal receipt.
- Calling the signer fails, and the successful quote path does not call it.
- `SIGTERM` writes no sell signal and no liquidation record.
- Receipts use `status: dry_run` under the dry-run directory only.

The optional public quote smoke stays out of that suite.

## Future live rules

This section binds a later spec. This phase does not implement it, and no flag turns it on.

A future live design must record the transaction signature durably before submit. It must not treat `getSignatureStatuses` returning null, at any commitment, as proof that a transaction is absent. While an intent is ambiguous, no new buy or order is created. A retry is allowed only after both of these are true: the transaction's validity window has expired on a finalized chain view, and reconciliation of that signature against finalized history and the wallet's finalized SOL and token balances shows the original attempt cannot still land. If those checks error, time out, or disagree, the process halts for manual resolution and sends no new order. Re-polling one already signed payload is not a new order. An HTTP retry is not exactly-once delivery. Losing the signed bytes is manual resolution, not a reason to call `/order` again.

A future live transaction must be checked for its effects: value moves only between SOL and that signal mint, with no transfer to another owner and no staking or lending program. Unknown decimals still reject the signal. Withdrawals stay out of scope.

Signing is unresolved and needs a separate approval. Jupiter `POST /execute` requires the signed transaction bytes. Phantom's existing `solana_send` signs and submits; it is not a sign-only interface. Isolated signing for `/execute` and a Phantom direct submit are different designs. This spec does not choose a local hot key.

External scanner integration beyond the inbox-copy contract stays deferred. Idempotent rejection of a duplicate `(id, side)` is journal behavior. It is not a fix for liquidation signals produced by an external scanner restart.

## Out of scope

Implementation and the implementation plan, live trading, `/execute`, `/build`, signing, broadcast, wallet creation, key export, Phantom sessions, withdrawals, staking, lending, arbitrary transfers, migrating the memedesk scanner, deployment, and restarting either process.
