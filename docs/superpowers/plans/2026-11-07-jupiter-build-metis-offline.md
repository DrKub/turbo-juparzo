# Metis Offline Decoder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add an isolated offline decoder that rejects synthetic unsigned legacy and v0 transactions for explicit structural reasons and never calls a transaction approved.

**Architecture:** A new `metis_offline` package parses standard-base64 wire bytes with `solders==0.29.0`, checks the header, zero signatures, fee payer, readonly flag, and address-lookup indexes, then always returns `kind="rejected"` with `instruction_semantics="unverified"`. It does not build, sign, send, or import `executor`.

**Tech Stack:** Python 3.12 and a separate virtualenv. Direct requirement `solders==0.29.0`. The resolved install also pins its declared dependencies `jsonalias==0.1.1` and `typing-extensions==4.16.0` in `requirements-metis-offline.txt`.

## Global Constraints

- This plan implements only the offline matrix in `docs/superpowers/specs/2026-11-07-jupiter-build-metis-design.md` at `ee6f979d474e5c16b9be6bb128ebe2b3a19b8521`. It does not approve the rest of candidate C.
- Do not modify `executor/`, `tests/test_*.py` outside `tests/test_metis_offline.py`, or `docs/superpowers/specs/2026-11-07-jupiter-dry-run-design.md`.
- No `/build`, `/execute`, `/submit`, RPC, ALT account fetch, `solana_send`, wallet, key, session, or environment secret.
- `slippage_bps` has no approved value. `None` and every integer, including the example `50`, return reason `slippage_unapproved`.
- A parsed message is not a safe swap. `instruction_semantics` is only `"unverified"`. `kind` is only `"rejected"`. The string `approved` is not a result value.
- Unknown program, unknown instruction data, missing lookup-table bytes, and any lookup index that cannot be resolved from bytes supplied in the test are reject reasons.
- The candidate spec's phrase "empty signature array" is read here as "every required signature slot is 64 zero bytes". A zero-length signature vector cannot carry the fee-payer slot, so it is `signature_count`, not a success.
- JupiterZ and `/execute` stay out. This package has no router client.

## Not in this plan

These remain separate future approvals. This plan does not claim compatibility with them:

- Public `GET /swap/v2/build` and compiling its response.
- RPC, live blockhash, and fetching address-lookup-table accounts.
- Phantom simulation, `confirmed: true`, and the user-present orchestrator.
- A signer, a hot key, a second fee payer, and `presignTransaction`.
- `/execute`, JupiterZ co-signing, and `/submit`.
- An approved `slippageBps`, live risk limits, and ambiguous-send reconciliation.
- A semantic decoder for swap instruction data or CPI.

## File structure

- Create: `requirements-metis-offline.txt` — `solders==0.29.0`, `jsonalias==0.1.1`, and `typing-extensions==4.16.0`.
- Create: `metis_offline/__init__.py` — exports `evaluate`.
- Create: `metis_offline/fixtures.py` — synthetic base64 and fake 32-byte keys. No real wallet and no live blockhash.
- Create: `metis_offline/report.py` — `DecodeReport` and reason constants.
- Create: `metis_offline/decode.py` — `evaluate(wire_b64: str, intent: Intent) -> DecodeReport`.
- Create: `tests/test_metis_offline.py` — the only new test module.
- Modify: `.gitignore` — add `.venv-metis-offline/`.

`Intent` fields are `user: bytes`, `input_mint: bytes`, `output_mint: bytes`, `amount: int`, `slippage_bps: int | None`, and `captured_tables: dict[bytes, bytes]`. `captured_tables` maps a lookup-table account key to the raw address bytes captured with a build. This phase's tests pass `{}`.

`DecodeReport` fields are `kind: str`, `parsed: bool`, `version: str | None`, `reason: str`, `fee_payer: bytes | None`, `signatures_all_zero: bool | None`, and `instruction_semantics: str`. `kind` is always `"rejected"`. `instruction_semantics` is always `"unverified"`.

Reason strings are `bad_base64`, `truncated`, `unsupported_version`, `signature_count`, `signature_not_zero`, `fee_payer`, `readonly`, `alt_missing`, `alt_index`, `unknown_program`, `slippage_unapproved`, and `intent_mismatch`.

## Runtime boundary

Existing tests stay on the system interpreter and do not import `solders`. The new tests run only in the separate environment:

```bash
python3 -m venv .venv-metis-offline
.venv-metis-offline/bin/pip install -r requirements-metis-offline.txt
.venv-metis-offline/bin/python -m unittest tests.test_metis_offline -v
.venv-metis-offline/bin/python -m compileall -q metis_offline tests/test_metis_offline.py
.venv-metis-offline/bin/python -m unittest discover -s tests -v
```

The last command is the full gate after the new package exists. It must be the virtualenv interpreter. System Python without `solders` cannot import `tests/test_metis_offline.py`; `python3 -m unittest discover -s tests` then reports one import error and still runs the older executor tests. Do not set `HTTP_PROXY`, API keys, or wallet variables for that install. `pip` may use the machine's existing proxy policy. Do not clear it.

`LEGACY_B64` is parsed by `solders` 0.29.0. The first published `V0_B64` began with `0x80` and had no signature vector, so `VersionedTransaction.from_bytes` raised `invalid transaction discriminator`. That string was replaced, with explicit approval, by the offline `solders==0.29.0` serialization from `metis_offline/synthetic.py`: one 64-byte zero signature, a 32-byte zero blockhash, and writable lookup index 7. Do not replace either fixture with a networked build.

---

### Task 1: Isolated package and report type

**Files:**
- Create: `requirements-metis-offline.txt`
- Create: `metis_offline/__init__.py`
- Create: `metis_offline/fixtures.py`
- Create: `metis_offline/report.py`
- Create: `tests/test_metis_offline.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: nothing from `executor`
- Produces: `DecodeReport`, `Intent`, `LEGACY_B64`, `V0_B64`, `NONZERO_SIG_B64`, `FEE_PAYER`, `PROGRAM_ID`, `DEAD_BLOCKHASH`

- [x] **Step 1: Write the failing test**

```python
import unittest

from metis_offline.fixtures import DEAD_BLOCKHASH, FEE_PAYER, LEGACY_B64, PROGRAM_ID
from metis_offline.report import DecodeReport


class ReportTest(unittest.TestCase):
    def test_report_cannot_say_approved(self):
        report = DecodeReport(False, None, "bad_base64", None, None)
        self.assertEqual(report.kind, "rejected")
        self.assertEqual(report.instruction_semantics, "unverified")
        self.assertNotEqual(report.kind, "approved")
        self.assertEqual(len(DEAD_BLOCKHASH), 32)
        self.assertEqual(DEAD_BLOCKHASH, bytes(32))
        self.assertEqual(len(FEE_PAYER), 32)
        self.assertEqual(len(PROGRAM_ID), 32)
        self.assertTrue(LEGACY_B64.startswith("AQAAAA"))


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_metis_offline -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'metis_offline'`

- [x] **Step 3: Write minimal implementation**

```text
# requirements-metis-offline.txt
solders==0.29.0
jsonalias==0.1.1
typing-extensions==4.16.0
```

```python
# metis_offline/fixtures.py
LEGACY_B64 = "AQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABAAECAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQECAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQEAAA=="
V0_B64 = "AQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACAAQABAgEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAEBAAABAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMBBwA="
NONZERO_SIG_B64 = "AQEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABAAECAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQECAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQEAAA=="
FEE_PAYER = bytes([1]) * 32
PROGRAM_ID = bytes([2]) * 32
TABLE_ID = bytes([3]) * 32
DEAD_BLOCKHASH = bytes(32)
```

`LEGACY_B64` is one zero signature, fee payer `0x01` repeated, program `0x02` repeated, and a 32-byte zero blockhash. `V0_B64` is the `solders` 0.29.0 wire form of the same accounts: signature count, 64 zero signature bytes, version byte `0x80`, then the message, plus lookup table `0x03` repeated whose writable index is 7. `metis_offline/synthetic.py` reproduces that string with no network. Neither fixture is a real swap.

```python
# metis_offline/report.py
from dataclasses import dataclass


@dataclass(frozen=True)
class Intent:
    user: bytes
    input_mint: bytes
    output_mint: bytes
    amount: int
    slippage_bps: int | None
    captured_tables: dict[bytes, bytes]


@dataclass(frozen=True)
class DecodeReport:
    parsed: bool
    version: str | None
    reason: str
    fee_payer: bytes | None
    signatures_all_zero: bool | None

    @property
    def kind(self) -> str:
        return "rejected"

    @property
    def instruction_semantics(self) -> str:
        return "unverified"
```

```python
# metis_offline/__init__.py
from metis_offline.report import DecodeReport, Intent

__all__ = ["DecodeReport", "Intent"]
```

Append `.venv-metis-offline/` to `.gitignore`.

- [x] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_metis_offline -v`

Expected: PASS, `Ran 1 test`. This step does not import `solders`.

- [x] **Step 5: Commit**

```bash
git add requirements-metis-offline.txt metis_offline/__init__.py metis_offline/fixtures.py metis_offline/report.py tests/test_metis_offline.py .gitignore
git commit -m "feat: add the offline Metis report types"
```

### Task 2: Base64, version, and zero signatures

**Files:**
- Create: `metis_offline/decode.py`
- Modify: `metis_offline/__init__.py`
- Modify: `tests/test_metis_offline.py`
- Create: `.venv-metis-offline/` by the commands below. Do not commit it.

**Interfaces:**
- Consumes: `DecodeReport`, `Intent`, `LEGACY_B64`, `V0_B64`, `NONZERO_SIG_B64`, `FEE_PAYER`
- Produces: `evaluate(wire_b64: str, intent: Intent) -> DecodeReport`

- [x] **Step 1: Write the failing test**

Append to `tests/test_metis_offline.py`:

```python
import base64

from metis_offline.decode import evaluate
from metis_offline.fixtures import FEE_PAYER, LEGACY_B64, NONZERO_SIG_B64, V0_B64
from metis_offline.report import Intent


def intent():
    return Intent(FEE_PAYER, bytes([4]) * 32, bytes([5]) * 32, 1, None, {})


class DecodeTest(unittest.TestCase):
    def test_bad_base64_and_truncated_do_not_parse(self):
        for raw in ("????", base64.b64encode(b"\x01\x02").decode("ascii")):
            report = evaluate(raw, intent())
            self.assertFalse(report.parsed)
            self.assertIn(report.reason, {"bad_base64", "truncated"})
            self.assertEqual(report.kind, "rejected")

    def test_nonzero_signature_rejects(self):
        report = evaluate(NONZERO_SIG_B64, intent())
        self.assertEqual(report.reason, "signature_not_zero")
        self.assertEqual(report.signatures_all_zero, False)

    def test_legacy_parses_then_rejects_unknown_program(self):
        report = evaluate(LEGACY_B64, intent())
        self.assertTrue(report.parsed)
        self.assertEqual(report.version, "legacy")
        self.assertEqual(report.signatures_all_zero, True)
        self.assertEqual(report.fee_payer, FEE_PAYER)
        self.assertEqual(report.reason, "unknown_program")
        self.assertEqual(report.instruction_semantics, "unverified")
```

Do not assert `V0_B64` in this task. Task 4 owns it.

- [x] **Step 2: Run test to verify it fails**

Run: `python3 -m venv .venv-metis-offline && .venv-metis-offline/bin/pip install -r requirements-metis-offline.txt && .venv-metis-offline/bin/python -m unittest tests.test_metis_offline.DecodeTest -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'metis_offline.decode'`

- [x] **Step 3: Write minimal implementation**

```python
# metis_offline/decode.py
import base64
import binascii

from solders.message import MessageV0
from solders.transaction import VersionedTransaction

from metis_offline.report import DecodeReport, Intent


def _reject(reason: str, parsed: bool = False, version: str | None = None, fee_payer: bytes | None = None, zeros: bool | None = None) -> DecodeReport:
    return DecodeReport(parsed, version, reason, fee_payer, zeros)


def evaluate(wire_b64: str, intent: Intent) -> DecodeReport:
    try:
        raw = base64.b64decode(wire_b64, validate=True)
    except (binascii.Error, ValueError):
        return _reject("bad_base64")
    if len(raw) < 1 + 64 + 3 + 32:
        return _reject("truncated")
    try:
        tx = VersionedTransaction.from_bytes(raw)
    except Exception:
        return _reject("truncated")
    version = "v0" if isinstance(tx.message, MessageV0) else "legacy"
    signatures = [bytes(sig) for sig in tx.signatures]
    header = tx.message.header
    if len(signatures) != header.num_required_signatures:
        return _reject("signature_count", True, version, None, None)
    zeros = all(sig == bytes(64) for sig in signatures)
    if not zeros:
        return _reject("signature_not_zero", True, version, None, False)
    fee_payer = bytes(tx.message.account_keys[0])
    if header.num_readonly_signed_accounts > 0:
        return _reject("readonly", True, version, fee_payer, True)
    if fee_payer != intent.user:
        return _reject("fee_payer", True, version, fee_payer, True)
    return _reject("unknown_program", True, version, fee_payer, True)
```

Export `evaluate` from `metis_offline/__init__.py`. Task 4 inserts the lookup-table checks immediately before this `unknown_program` return.

- [x] **Step 4: Run test to verify it passes**

Run: `.venv-metis-offline/bin/python -m unittest tests.test_metis_offline -v`

Expected: PASS. `LEGACY_B64` parses as legacy and returns `unknown_program`. `NONZERO_SIG_B64` returns `signature_not_zero`.

- [x] **Step 5: Commit**

```bash
git add metis_offline/decode.py metis_offline/__init__.py tests/test_metis_offline.py
git commit -m "feat: reject malformed offline transaction bytes"
```

### Task 3: Fee payer and readonly flag

**Files:**
- Modify: `tests/test_metis_offline.py`
- Modify: `metis_offline/decode.py` only if Step 4 fails because the header check is incomplete. The Task 2 function already returns `fee_payer` and `readonly`.

**Interfaces:**
- Consumes: `evaluate`, `Intent`, `LEGACY_B64`, `FEE_PAYER`
- Produces: no new names

- [x] **Step 1: Write the failing test**

```python
    def test_wrong_fee_payer_and_readonly_signer_reject(self):
        wrong = Intent(bytes([9]) * 32, bytes([4]) * 32, bytes([5]) * 32, 1, None, {})
        report = evaluate(LEGACY_B64, wrong)
        self.assertEqual(report.reason, "fee_payer")
        self.assertEqual(report.fee_payer, FEE_PAYER)
        mutable = bytearray(base64.b64decode(LEGACY_B64))
        # Message header starts after the one 64-byte zero signature.
        mutable[1 + 64 + 1] = 1
        readonly = base64.b64encode(bytes(mutable)).decode("ascii")
        flagged = evaluate(readonly, intent())
        self.assertEqual(flagged.reason, "readonly")
```

`mutable[1 + 64 + 1]` is `num_readonly_signed_accounts`. Setting it to 1 marks the only signer readonly.

- [x] **Step 2: Run test to verify it fails**

Run: `.venv-metis-offline/bin/python -m unittest tests.test_metis_offline.DecodeTest.test_wrong_fee_payer_and_readonly_signer_reject -v`

Expected: FAIL because the new method is missing, then PASS after the method is added if Task 2's order is already fee payer before unknown program. If the failure is `AssertionError: 'unknown_program' != 'fee_payer'`, the implementation order is wrong. Move the `fee_payer` and `readonly` returns above `unknown_program`. Do not weaken the assertions.

- [x] **Step 3: Write minimal implementation**

Keep the order in `evaluate`: signature count, zero signatures, readonly signed accounts, fee payer, lookup tables, then `unknown_program`. No other change if Step 2 passes once the test method exists.

- [x] **Step 4: Run test to verify it passes**

Run: `.venv-metis-offline/bin/python -m unittest tests.test_metis_offline -v`

Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add metis_offline/decode.py tests/test_metis_offline.py
git commit -m "feat: reject a mismatched fee payer and readonly signer"
```

### Task 4: Lookup tables and unknown programs

**Files:**
- Modify: `metis_offline/decode.py`
- Modify: `tests/test_metis_offline.py`

**Interfaces:**
- Consumes: `evaluate`, `V0_B64`, `TABLE_ID`, `Intent.captured_tables`
- Produces: reasons `alt_missing` and `alt_index` before `unknown_program`

- [x] **Step 1: Write the failing test**

```python
from metis_offline.fixtures import TABLE_ID, V0_B64

    def test_v0_without_captured_table_is_alt_missing(self):
        report = evaluate(V0_B64, intent())
        self.assertEqual(report.version, "v0")
        self.assertEqual(report.reason, "alt_missing")
        self.assertEqual(report.instruction_semantics, "unverified")

    def test_lookup_index_past_captured_table_is_alt_index(self):
        short = Intent(FEE_PAYER, bytes([4]) * 32, bytes([5]) * 32, 1, None, {TABLE_ID: bytes(32)})
        report = evaluate(V0_B64, short)
        self.assertEqual(report.reason, "alt_index")
```

`V0_B64` asks for writable index 7. A captured table of 32 bytes holds one address, so index 7 is out of range. The table bytes are synthetic. They are not fetched.

- [x] **Step 2: Run test to verify it fails**

Run: `.venv-metis-offline/bin/python -m unittest tests.test_metis_offline.DecodeTest.test_v0_without_captured_table_is_alt_missing tests.test_metis_offline.DecodeTest.test_lookup_index_past_captured_table_is_alt_index -v`

Expected: FAIL with `AssertionError` because Task 2 returns `alt_missing` for every lookup and never `alt_index`.

- [x] **Step 3: Write minimal implementation**

Replace the lookup return in `evaluate` with:

```python
    if isinstance(tx.message, MessageV0):
        for lookup in tx.message.address_table_lookups:
            table_key = bytes(lookup.account_key)
            captured = intent.captured_tables.get(table_key)
            if captured is None:
                return _reject("alt_missing", True, version, fee_payer, True)
            if len(captured) % 32 != 0:
                return _reject("alt_missing", True, version, fee_payer, True)
            limit = len(captured) // 32
            indexes = list(lookup.writable_indexes) + list(lookup.readonly_indexes)
            if any(index >= limit for index in indexes):
                return _reject("alt_index", True, version, fee_payer, True)
```

A captured table that covers every index still falls through to `unknown_program`. Resolving an index does not approve the instruction. There is no RPC call.

- [x] **Step 4: Run test to verify it passes**

Run: `.venv-metis-offline/bin/python -m unittest tests.test_metis_offline -v`

Expected: PASS. `V0_B64` with empty `captured_tables` is `alt_missing`. The same bytes with a 32-byte table are `alt_index`.

- [x] **Step 5: Commit**

```bash
git add metis_offline/decode.py tests/test_metis_offline.py
git commit -m "feat: reject unresolved address lookup tables"
```

### Task 5: Intent and unapproved slippage

**Files:**
- Modify: `metis_offline/decode.py`
- Modify: `tests/test_metis_offline.py`

**Interfaces:**
- Consumes: `evaluate`, `Intent`, `LEGACY_B64`
- Produces: reasons `slippage_unapproved` and `intent_mismatch`. Neither is reachable on `LEGACY_B64` until the program is known, which this phase never grants. The tests therefore call a helper `check_slippage(intent: Intent) -> str` that the public `evaluate` also uses when structure would otherwise fall through a known program. Because no program is known, `evaluate` on `LEGACY_B64` stays `unknown_program`. `check_slippage` is the explicit gate.

- [x] **Step 1: Write the failing test**

```python
from metis_offline.decode import check_amount, check_slippage

    def test_example_slippage_is_not_approved(self):
        self.assertEqual(check_slippage(intent()), "slippage_unapproved")
        example = Intent(FEE_PAYER, bytes([4]) * 32, bytes([5]) * 32, 1, 50, {})
        self.assertEqual(check_slippage(example), "slippage_unapproved")

    def test_amount_mismatch_is_not_a_swap_approval(self):
        report = evaluate(LEGACY_B64, intent())
        self.assertEqual(report.reason, "unknown_program")
        self.assertNotEqual(report.kind, "approved")
```

`50` is an example integer from Jupiter's build table. It is not an accepted policy.

- [x] **Step 2: Run test to verify it fails**

Run: `.venv-metis-offline/bin/python -m unittest tests.test_metis_offline.DecodeTest.test_example_slippage_is_not_approved -v`

Expected: FAIL with `ImportError: cannot import name 'check_slippage'`

- [x] **Step 3: Write minimal implementation**

```python
def check_slippage(intent: Intent) -> str:
    del intent
    return "slippage_unapproved"


def check_amount(intent: Intent, actual: int) -> str:
    if actual != intent.amount:
        return "intent_mismatch"
    return check_slippage(intent)
```

`evaluate` does not call these for `LEGACY_B64`, because the empty program allowlist already returned `unknown_program`. A later allowlist must call `check_amount` before returning. Add the amount assertion beside the slippage test:

```python
    def test_matching_amount_is_still_not_approved(self):
        self.assertEqual(check_amount(intent(), 2), "intent_mismatch")
        self.assertEqual(check_amount(intent(), 1), "slippage_unapproved")
```

Add this source boundary assertion in the same test file:

```python
    def test_package_has_no_network_client(self):
        from pathlib import Path
        text = "\n".join(path.read_text(encoding="utf-8") for path in Path("metis_offline").rglob("*.py"))
        self.assertNotIn("urlopen", text)
        self.assertNotIn("/execute", text)
        self.assertNotIn("/build", text)
        self.assertNotIn("solana_send", text)
```

- [x] **Step 4: Run test to verify it passes**

Run: `.venv-metis-offline/bin/python -m unittest tests.test_metis_offline -v`

Expected: PASS. Every report is `rejected` and `unverified`.

- [x] **Step 5: Commit**

```bash
git add metis_offline/decode.py tests/test_metis_offline.py
git commit -m "feat: reject unapproved slippage without approving a swap"
```

### Task 6: Full offline gate

**Files:**
- Modify: none, unless a prior test failed.

**Interfaces:**
- Consumes: the package from Tasks 1 through 5
- Produces: the recorded command output. No new API.

- [x] **Step 1: Run the new module and the existing suite**

Run:

```bash
.venv-metis-offline/bin/python -m compileall -q metis_offline tests/test_metis_offline.py
.venv-metis-offline/bin/python -m unittest tests.test_metis_offline -v
.venv-metis-offline/bin/python -m unittest discover -s tests -v
```

Expected: `compileall` prints nothing and exits 0. `test_metis_offline` passes. `discover` passes and includes the previous executor tests. A failure in an old executor test means this package imported or edited `executor`. Revert that edit.

- [x] **Step 2: Commit only if Step 1 required a fix**

```bash
git add metis_offline tests/test_metis_offline.py requirements-metis-offline.txt .gitignore
git commit -m "test: record the offline Metis decoder gate"
```

If Step 1 passes with a clean tree, do not create an empty commit.

## What this phase proves

It proves that one pinned decoder rejects the synthetic fixtures for the reasons in the tests: bad base64, truncation, a non-zero signature, a readonly fee payer, the wrong fee payer, a missing or short lookup table, an unknown program, and any slippage value. The legacy and v0 fixtures are parsed far enough to name the version and the fee payer, and are still rejected. A legacy blob whose signature-count byte is zero and whose parsed account list is empty returns `truncated`. It does not raise. A message with no instructions still returns `slippage_unapproved` for `None` and for the example `50`. A version prefix other than v0, such as `0x81`, returns `unsupported_version`. An empty signature vector on a message that requires a signer returns `signature_count`.

## What this phase does not prove

It does not prove a swap is safe, that instruction data or CPI does what the caller intends, that a lookup table's on-chain bytes match a build, that `solana_send` accepts the bytes, or that a signature is final. It does not choose `slippageBps`. It does not call Jupiter or Phantom. A later live design still needs its own approval.

The three previously recorded gaps are closed in the offline decoder. `evaluate` calls `check_slippage` when no instruction is present, and neither `None` nor `50` is approved. An instruction whose program is not on the empty allowlist is still `unknown_program`, including when slippage is `50`. A non-zero version prefix is `unsupported_version`, while a short byte string stays `truncated`. `signature_count` has a regression test. None of these results is `approved`.
