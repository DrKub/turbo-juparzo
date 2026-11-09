# Offline Metis preflight

`./metis-offline` reads one unsigned Jupiter transaction plus on-chain evidence and prints a JSON report. It is standard-library Python (system `python3`): no virtualenv, no `solders`, no network, no signing. The launcher sets `PYTHONPATH` to the repository root; `METIS_PYTHON` overrides the interpreter.

```bash
./metis-offline metis_offline/samples/synthetic-direct-route.json   # synthetic, exits 0
./metis-offline - < request.json
```

## Request

```json
{
  "transaction_b64": "<standard base64 of the unsigned v0 transaction>",
  "intent": {"user": "<base58>", "input_mint": "So111...112", "output_mint": "EPjF...Dt1v",
             "amount": 1000000, "slippage_bps": 50, "max_priority_fee_lamports": 10000},
  "lookup_tables": [{"address": "<base58>", "owner": "<base58 owner from getAccountInfo>",
                     "data_b64": "<full account data from getAccountInfo, base64>",
                     "context_slot": 123}],
  "freshness": {"blockhash": "<base58>", "last_valid_block_height": 1, "current_block_height": 0}
}
```

A body that does not start with `{` is treated as bare base64: it is decoded structurally and rejected with `intent_missing`.

Keep request files outside git (`*.b64` and `metis-request*.json` are ignored). Stdout never echoes the transaction bytes.

## What a pass means

Exit `0`, `kind: semantic_pass`, `reason: ok_unsigned` means every check below passed on an **unsigned** build. `trade_authorized` is always `false` and `signatures_authentic` is always `false`. Exit `2` is a rejection with a `reason`. Exit `1` is a malformed request.

Hard limits (constants in `metis_offline/preflight.py`; a request can only tighten them): amount <= 1,000,000 lamports, slippage <= 50 bps, priority fee <= 10,000 lamports. Only the pair SOL (wrapped) -> canonical USDC `EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v` is supported.

Checks, in order:

1. Wire: v0 only, exactly one required signature (the payer), no read-only signer, every signature slot zero (`signature_present` otherwise), fee payer = `intent.user`.
2. Lookup tables, from supplied account data, for every table the message references: owner is `AddressLookupTab1e1111111111111111111111111` (`alt_owner`); 56-byte header with state type 1 and a whole number of 32-byte addresses (`alt_layout`); deactivation slot = u64 max (`alt_deactivated`); last-extended slot not after `context_slot` and addresses extended in the context slot excluded (`alt_context`); every index inside the usable range (`alt_index`); missing table (`alt_missing`).
3. Outer instructions, closed allowlist (anything else is `unknown_program` / `unsupported_instruction`):
   - ComputeBudget: at most one SetComputeUnitLimit, at most one SetComputeUnitPrice, optional SetLoadedAccountsDataSizeLimit.
   - Associated Token Account create / create-idempotent, payer and owner = user, Tokenkeg only, for the user's WSOL ATA or the user's USDC ATA (addresses derived offline).
   - One System transfer of exactly `amount` from the user to the user's WSOL ATA; any other transfer is `unexpected_transfer` (tips included).
   - Token SyncNative on the WSOL ATA, and CloseAccount of the WSOL ATA to the user with the user as owner. Approve / ApproveChecked is `approve_forbidden`.
   - Exactly one Jupiter `route_v2` (discriminator `[187,100,250,204,49,196,175,20]`): `in_amount` at data offset 8 = intent amount, `quoted_out_amount` at 16 (> 0), `slippage_bps` at 24 = intent slippage and <= 50, platform fee and positive-slippage fee 0, route plan length 1 with the step moving 10000 bps from input slot 0. Accounts 0-9 must be user, user WSOL ATA, user USDC ATA, WSOL mint, USDC mint, Tokenkeg x2, Jupiter (no separate destination), Jupiter event authority, Jupiter. Exactly one known AMM program must appear in the hop accounts (`unsupported_amm`). Other Jupiter instructions (legacy `route`, shared-accounts variants) are `unsupported_route`.
   - Order: WSOL ATA create < transfer < sync < route < close; a USDC ATA create, if present, comes before the route.
4. Fees: base = 5,000 x signatures. Priority = ceil(price x limit / 1,000,000). The limit is the explicit SetComputeUnitLimit (clamped at 1,400,000) when present; otherwise the default: 200,000 per non-builtin instruction plus 3,000 per builtin (System, ComputeBudget, AddressLookupTable), capped at 1,400,000. An absent price means priority 0.
5. Freshness: `freshness.blockhash` must equal the message blockhash, and `current_block_height <= last_valid_block_height` (block height, not slot), else `stale_blockhash`.

## Decision: intermediate token accounts (route without reserve)

A previous multi-hop route (SOL -> USDT -> USDC) created the user's USDT ATA and never closed it, leaving 1,488,440 lamports of rent behind. Decision: request a **direct** route and refuse anything else.

- The build helper requests `onlyDirectRoutes=true&instructionVersion=V2` from the Jupiter quote API.
- The preflight accepts only a single-step route plan, and only these account creations: the user's WSOL ATA, which must be closed back to the user later in the same transaction, and the user's USDC destination ATA (create-idempotent; a no-op when it exists, otherwise it keeps rent because it holds the output). Any other ATA create, System CreateAccount, or Token InitializeAccount fails closed with `unsupported_intermediate_account`.
- The tool never closes or sweeps a pre-existing account.

## Known gaps (not verified offline)

- The AMM hop's CPI instruction data is not decoded; only the venue program is allowlisted. Inner effects are evidenced by the read-only simulation in `tools/jupiter_unsigned_build.py` (token deltas, inner account creations), not by this tool.
- Signatures are never verified; the tool only accepts an all-zero (unsigned) build.
- Whether the USDC ATA already exists is not known offline; the report flags a create-idempotent as `usdc_destination_ata_create_idempotent`.

## Fresh unsigned build helper

`python3 tools/jupiter_unsigned_build.py [--out DIR]` (network, read-only): quote + build from `lite-api.jup.ag/swap/v1` (direct route, V2 instruction, 0.001 SOL, 50 bps, priority `maxLamports` 10,000), lookup tables via `getAccountInfo`, block height, runs `./metis-offline`, then `simulateTransaction` with `sigVerify:false, replaceRecentBlockhash:false`, and re-checks block height and `isBlockhashValid`. It refuses any RPC method outside a read-only allowlist and writes bytes only under `/tmp/jupiter-unsigned` by default. It never signs or sends.

### Venue restriction

The unsigned build helper passes `dexes=Meteora DLMM` to the Jupiter quote so the direct route uses a venue on the preflight AMM allowlist. Without it, Jupiter returned direct routes via Kipseli and 1DEX, which the preflight correctly rejected with `unsupported_amm`.

## Live flow and sign gate

Hard rule: a transaction is signed and sent only **immediately** after a successful semantic check **and** a successful read-only simulation of the exact same message, with no pause or manual step in between. The signer (Phantom embedded wallet via Phantom MCP) is driven by the assistant, not by this repository, so the repository enforces the rule with a fail-closed gate. Nothing in this repository holds keys, signs, or sends; `tools/readonly_rpc.py` refuses every RPC method outside its read-only allowlist (`sendTransaction`, `sendRawTransaction`, `requestAirdrop` are refused).

Flow:

1. `python3 tools/live_once.py [--out DIR]` runs, with no pause: fresh direct-route build (`tools/jupiter_unsigned_build.py`) -> `./metis-offline` -> read-only simulation -> `tools/sign_gate.py`. Exit 0 prints `unsigned_b64_path`, the ticket and `ticket_path`; exit 2 prints the denial reason.
2. Only on exit 0, and before `ticket.expires_at`, the external signer signs and sends exactly the bytes in `unsigned_b64_path`.
3. `python3 tools/record_result.py SIGNATURE --ticket TICKET_PATH` fetches `getTransaction` read-only and logs the signature and the on-chain result, including whether the on-chain message sha256 equals the ticket's and whether it landed within the ticket TTL.

`python3 tools/sign_gate.py BUILD_DIR` can also be run on its own. Gate rules, in order; any failure denies (exit 2) and is logged:

1. `summary.json`, `unsigned.b64`, `metis-request.json` exist and parse.
2. The request carries exactly the bytes in `unsigned.b64`.
3. Saved check: metis exit 0, `semantic_pass`, `ok_unsigned`, `trade_authorized` false.
4. `message_sha256` recomputed from `unsigned.b64` equals the check's and the simulation's (the simulation ran on the same message).
5. Simulation `err` null, no persistent account created, WSOL ATA closed, USDC delta >= `min_out_amount_floor`.
6. Summary age <= 30 s.
7. `last_valid_block_height` in the request equals the build's.
8. Fresh `getBlockHeight`: at least 45 blocks of margin before `last_valid_block_height`.
9. The preflight is re-run in-process with that fresh height and passes again.
10. `isBlockhashValid` is true.
11. Single use: a consume marker `data/sign-gate/consumed/<message_sha256>` is created exclusively; a second approval of the same message is `ticket_reused`.

An approval issues a ticket valid for 20 s (`ticket_id`, `message_sha256`, `unsigned_sha256`, `blockhash`, `last_valid_block_height`, `blocks_left`, `issued_at`, `expires_at`). Gate state lives in `data/sign-gate/` (git-ignored).

## Operation log

`logs/operations.jsonl` is append-only, one JSON object per line, committed to git: `ts` (Europe/Kyiv), `op` (`build`, `check`, `simulate`, `gate`, `sign`, `result`, `note`), `message_sha256`, `backfilled`, `data`. The build helper writes build/check/simulate entries, the gate writes every decision, and `record_result.py` writes sign and result entries (slot, block time, err, fee, compute units, signer SOL delta, signer token deltas by mint).

`tools/oplog.py` refuses to write an entry containing raw transaction bytes or secrets: keys such as `transaction_b64`, `swapTransaction`, `data_b64`, `wire`, `raw`, any secret-named key, raw `bytes`, or any string longer than 100 characters made only of base64/base58 characters. Public keys, signatures and hashes are allowed. The first entries backfill the 2026-11-09 12:50 Kyiv live test (signature `57QRfRyxVsjfWhpvwfLXPteaoW9qunAGK1x9B1m991t6Rr8odyyRJDx8pCu8GcYQNWfe5ok3u7juPcWMeJa9svuS`) and are marked `backfilled`.
