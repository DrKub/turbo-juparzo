# Jupiter Build/Metis candidate

Status: design candidate only, written 2026-11-07. This file does not approve a live design, an implementation, a plan, a wallet call, or a trading mandate.

The dry-run specification `docs/superpowers/specs/2026-11-07-jupiter-dry-run-design.md` and the `executor` package stay as they are. The expired mandate of 2026-11-07 08:00 Europe/Kiev remains expired. No flag in that package can revive it.

Nothing in this candidate is live-ready. A later, separate approval is required before any `/build` request, any `taker`, any RPC, any `solana_send`, or any broadcast.

## Current facts

The implemented executor asks `GET https://api.jup.ag/swap/v2/order` with no `taker`. That response has `transaction: null`. It therefore contains no unsigned transaction bytes to decode, sign, or send.

On 2026-11-07 the connected Phantom tool catalog, as supplied for this design and not re-invoked here, has:

- `solana_send`, which signs and broadcasts. With `confirmed` false it only simulates and returns `pending_confirmation`. With `confirmed: true`, and only after preview approval, it returns `{signature, networkId, account}`.
- `solana_sign`, which signs a UTF-8 off-chain message, not a Solana transaction.
- no `sign_transaction` tool.

This cloud session cannot call that connector. It must not read the connector's sessions, tokens, or keys, and it must not pretend that a standalone process or this cloud agent can invoke the user's MCP. A future user-present orchestrator, running where that connector is actually attached, would be a different program and needs its own approval. Until that access path exists, unattended 24/7 trading is not a supported claim.

Official sources checked for this candidate:

- https://developers.jup.ag/docs/swap
- https://developers.jup.ag/docs/swap/order-and-execute
- https://developers.jup.ag/docs/swap/build
- https://developers.jup.ag/docs/api-reference/swap/execute
- https://docs.phantom.com/sdks/browser-sdk/sign-and-send-transaction
- https://docs.phantom.com/phantom-deeplinks/provider-methods/signtransaction

## Three approaches

### A. Stay on quote-only

Keep the current executor. It never sets `taker`, never receives transaction bytes, and never signs. This is the only path implemented today. It cannot become a live swap without a new design.

### B. `/order` plus a separately approved sign-only surface, then `/execute`

`GET /order` with `taker` returns an assembled transaction. The documented landing path is `POST /swap/v2/execute` with `signedTransaction` and `requestId`. JupiterZ needs a partial user signature because the market maker co-signs during `/execute`. Those transactions must not be modified, and they cannot be completed by broadcasting only the user's signature. Aggregator routes expire at `lastValidBlockHeight`. JupiterZ expires at `expireAt`, which is shorter.

The connected Phantom catalog cannot supply the signed bytes `/execute` requires. Phantom's SDK `signTransaction` is a different surface. The SDK documents that embedded wallets do not support `signTransaction` and must use `signAndSendTransaction`, which also broadcasts. Choosing B requires a new sign-only connection the user has not attached, plus a separate approval to call `/execute`.

### C. Candidate: `/build` and Metis only, later Phantom simulate then user approval then send

`GET /swap/v2/build` returns Metis instructions, not an assembled JupiterZ transaction. The documented landing choices are the caller's own RPC or Jupiter `/submit`. `/build` transactions cannot use `/execute`: there is no `requestId`, and `/execute` rejects modifications. JupiterZ, Dflow, and OKX do not route this path. Jupiter charges no swap fee on `/build`. Integrator fees exist only if `platformFeeBps` and `feeAccount` are set. This candidate sets neither.

This is the candidate to review. It is not selected for implementation.

| | A quote-only | B `/order` + sign-only + `/execute` | C `/build` Metis |
| --- | --- | --- | --- |
| Routers | none, quote only | Metis, JupiterZ, Dflow, OKX | Metis only |
| Landing | none | `/execute` | future wallet broadcast or `/submit`, never `/execute` |
| Needs sign-only bytes | no | yes | no, if the wallet signs and sends |
| Connected Phantom enough | yes, because it is not called | no | not from this process; only from a future user-present orchestrator |
| Unattended trading | no | no | no |

## Orchestration boundary

A future Build/Metis flow, if later approved, has two separate programs:

1. A builder that turns a `/build` response into one canonical unsigned transaction. It holds no wallet session.
2. A user-present orchestrator that shows that exact transaction, asks the attached Phantom connector for `solana_send` with `confirmed: false`, shows the simulation, and only after a fresh user approval calls `solana_send` with `confirmed: true`.

The builder cannot perform step 2. The cloud agent cannot perform step 2. Copying a session token or calling the connector endpoint by hand is out of scope and forbidden. `confirmed: false` is still a wallet-tool call and is not authorized by this file. There is no supported way, in the current attachment, for a detached process to trade all day.

## What C would have to prove later

This section binds a future spec. It authorizes no code.

The builder compiles one versioned transaction from the `/build` response. The default version is v0. v0 uses `addressesByLookupTableAddress` as address-lookup tables. v1 is fail-closed until a named wallet surface is shown to accept v1. Jupiter's build guide says not every wallet extension signs v1. The connected `solana_send` schema does not state a transaction version, so v1 is not assumed.

Wire format is standard base64 of the serialized unsigned transaction. Whether that encoding is the same `standard-base64` `solana_send` accepts is untested. A future offline test has to show a decode of the same bytes the orchestrator would hand over. A mismatch fails closed. No second encoding is invented here.

The fee payer is the user's Phantom account. This candidate does not introduce a local hot key, a second payer, or a `presignTransaction` callback. Phantom's embedded-wallet guide says a pre-signed transaction is not accepted and a second signer has to go through that callback. That callback is a different design and is not chosen.

The swap is SOL to the signal mint, or that mint back to SOL, for the exact base-unit amount already accepted by the dry-run rules. Slippage is an explicit `slippageBps` recorded with the request. The default written by Jupiter's build table is 50 bps. This candidate does not silently adopt it as a risk decision. The future spec must name the value. `platformFeeBps` stays 0 and `feeAccount` stays unset. Rent and wrap/unwrap SOL are allowed only as instructions required by that returned swap, with the fee payer equal to the user. They are not a general SOL transfer.

Before any handoff the builder records the canonical bytes and rejects the transaction if any of these fail:

- The version is not the accepted v0.
- A lookup-table index points outside the tables returned with this build. Tables are not fetched from RPC in order to fill a gap.
- An instruction program is outside the set returned for this build: compute budget, the token program required by the mint, associated-token program, system program only for the wrap or unwrap that belongs to this swap, and the swap program Jupiter returned. Any other program fails closed.
- A transfer, approval, delegate, close-account, or authority change moves value or authority to an owner other than the user and the accounts Jupiter named for this swap.
- The input and output mints or amounts differ from the request.
- The fee payer is not the user, or an unexpected signer is required.
- The blockhash lifetime is missing. Jupiter's `blockhashSlotsToExpiry` default is 150 slots, with a documented range of 1 to 300. Past that lifetime the bytes are expired and must be discarded. A new `/build` is a new transaction, not a retry of the old bytes.

The dry-run risk numbers remain candidates for that future check, not live limits: at most 8 percent of the simulated bank, at least 0.03 SOL left after the stated fee and rent, at most three open mints, one open position per mint, stop at 6 percent, trailing armed at 12 percent with a 6 percent giveback. Activating them on mainnet is a separate decision. They do not by themselves make a transaction safe.

## Bytes are not a safety proof

A decoder can show the version, the declared signers, the fee payer, the instruction list, and the account indexes. It cannot show that a known program's opaque instruction data is a swap of the intended size, that a lookup table still contains the accounts it contained at build time, or that the route cannot move value to another owner. Those checks need the instruction data decoded against the specific program and the lookup-table bytes captured with the build. If either decode is incomplete, the result is reject. A transaction that parses cleanly is still not an approved swap.

## Send, ambiguity, and reconciliation

`solana_send` with `confirmed: true` returns a signature after broadcast. That signature is not finalization. A response that is lost, times out, or disagrees with a later status is ambiguous. Ambiguous means no new build and no new send. Reconciliation has to use a finalized chain view and the wallet's finalized balances. A null signature status is not proof the transaction is absent. Retrying the same signed bytes is not a new order. Building a fresh `/build` while the old signature is ambiguous is forbidden. HTTP retry is not exactly-once delivery.

This path still does not call `/execute`. Mixing a Metis `/build` transaction with JupiterZ or with `/execute` is a design error, not a fallback.

## Offline check

No transaction decoder is available in this repository or in the current environment. `solana` and `solders` are not installed. No decoder was run. No `/build` request was made, and `solana_send` was not called, including with `confirmed: false`.

The first implementation, after a separate approval, starts with this matrix on one synthetic unsigned fixture. The fixture is not a real swap: it has no live blockhash, no real wallet, and no mainnet account that would make it broadcastable. The matrix is the specification of the test, not the test code.

| Case | Required result |
| --- | --- |
| Truncated or non-base64 input | Reject before any signer check |
| Legacy, v0, and v1 headers | Accept only the one version named by the future spec, now proposed as v0 |
| Signature array empty, partial, or already populated | Unsigned handoff requires the empty case. Anything else rejects |
| Fee payer differs from the named user | Reject |
| Extra instruction or unknown program id | Reject |
| System or token transfer to an owner outside the swap account list | Reject |
| Set-authority, approve, or close-account beyond the swap | Reject |
| Lookup-table index out of range, or a table omitted from the captured response | Reject |
| Input mint, output mint, or amount different from the request | Reject |
| Expired or missing blockhash lifetime | Reject and do not requote automatically inside the same approval |
| Fixture that passes every row above | Still not evidence of a safe swap. The report says structural accept only |

No row sends, signs, or contacts a network.

## Open decisions

- The numeric `slippageBps` is unnamed.
- Whether `solana_send` accepts Jupiter's standard base64 and v0 transactions is untested.
- Who runs the user-present orchestrator, and how the user approves each `confirmed: true`, is undesigned.
- `/submit` with a SOL tip is not part of this candidate. It would be another landing design.
- The dry-run risk numbers are not promoted to live limits.

## Next approval boundary

The next human step is to accept, reject, or edit this candidate. Acceptance still does not allow implementation, a `/build` call, a wallet simulation, or a plan. A plan starts only after a later explicit approval of the written spec.
