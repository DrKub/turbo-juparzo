# Jupiter Dry-Run Executor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Build one offline-tested Python process that turns copied memedesk JSON signals into isolated Jupiter quote-only dry-run receipts.

**Architecture:** A single-threaded `python3 -m executor` process refuses live configuration, takes one OS lock, folds a durable journal into a simulated book, validates inbox copies, requests `GET /swap/v2/order` with no `taker`, and commits either a `status: dry_run` journal file or nothing. Stop and trailing marks use the same quote adapter. The journal file is the only receipt.

**Tech Stack:** Python 3.12 standard library and `unittest` only. No third-party packages, no Solana SDK, no Jupiter SDK, no HTTP library beyond `urllib.request` for the real quote transport.

## Global Constraints

Every task includes these rules. Values are copied from the approved spec.

- Approval recorded for this phase: «Утверждаю проект: сначала тестовый режим без ключей и реальных сделок».
- The previous live mandate expired at 2026-11-07 08:00 Europe/Kiev (`2026-11-07T08:00:00+02:00`). Expiry is final. No environment variable, CLI flag, config file, later expiry, or default can sign, submit, or revive that mandate. This phase accepts no replacement mandate.
- Dry-run receipts use `"status": "dry_run"` and live only under the executor directory. They are not scanner fills.
- The only network call this design allows is `GET https://api.jup.ag/swap/v2/order` with query parameters `inputMint`, `outputMint`, and `amount`. The request omits `taker` and omits `x-api-key`.
- `POST /swap/v2/execute` requires `signedTransaction` and `requestId`. This phase never calls it. `/build` is not called.
- Exit code 3, before the lock, for live, mandate, signing, or invalid closed-config refusal. Exit code 2 when the lock is busy, before a quote or a write.
- Cap: reject when `buy_lamports * 100 > bank_lamports * 8`. Exactly 8 percent passes.
- Reserve: `post = bank_lamports - buy_lamports - fee - rent`. Reject when `post < 30000000`. Exactly `30000000` lamports (0.03 SOL) passes.
- At most three open positions. A second open position on the same mint is rejected. After a committed full close, a later buy may open that mint again.
- Stop when `mark_out_lamports * entry_out_base_units * 100 <= entry_buy_lamports * mark_in_base_units * 94`.
- Arm when `mark_out_lamports * entry_out_base_units * 100 >= entry_buy_lamports * mark_in_base_units * 112`.
- Giveback, only if already armed, when `mark_out_lamports * high_in_base_units * 100 <= high_out_lamports * mark_in_base_units * 94`.
- SOL mint is `So11111111111111111111111111111111111111112`.
- Signal `id` matches `^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$` and does not start with `rexit.`.
- The public agent wallet `8Xm6iFvk9J8rBi7BvRAbiZU2RAEE1b7AM714QmiEXQ8a` is context only. This phase does not hardcode it, does not send it as `taker`, and does not sign for it.
- Default tests use a fake transport and zero network. The optional smoke command stays out of that suite.
- `data/jupiter-dry-runs/` is runtime state and must be gitignored. Receipts must not be written anywhere else.
- Do not modify `README.md`. Do not migrate `/workspace/memedesk`. Do not implement the spec section "Future live rules".
- A missing buy estimate uses reason `risk_reserve`. The spec's closed reason list has no separate code, and the reserve cannot be computed without the estimate.
- Every journal receipt includes `seq` plus the spec fields. `seq` is the commit order the fold needs when a mint is bought again after a close. It is not a second store.

## File structure

- `executor/__init__.py` — package marker. No process logic.
- `executor/__main__.py` — `python3 -m executor` calls `executor.cli.main`.
- `executor/types.py` — constants, ids, and frozen dataclasses shared by every module.
- `executor/integers.py` — canonical integer strings and exact `size_sol` lamport conversion.
- `executor/config.py` — closed config loader and base58 mint check.
- `executor/schema.py` — one inbox object becomes a `Signal` or a `SchemaRejection`.
- `executor/startup.py` — argv and environment refusal before any lock, inbox read, or socket.
- `executor/signer.py` — `sign` always raises `SignerRefusal`.
- `executor/records.py` — JSON for a receipt and a mark. Hardcodes `"status": "dry_run"`.
- `executor/risk.py` — pre-quote reason, or `None` when a quote is allowed.
- `executor/marks.py` — `decide_mark` and `apply_observation`.
- `executor/quote.py` — URL, classifier, and urllib transport used only after refusal and lock.
- `executor/journal.py` — `fsync`, rename, temp cleanup, and load.
- `executor/book.py` — fold receipts and marks into `Book`.
- `executor/lock.py` — non-blocking exclusive `flock`.
- `executor/run.py` — one invocation. No signer call and no live branch.
- `executor/cli.py` — process entry, exit codes, and `SIGTERM` flag.
- `executor/smoke.py` — one optional public GET. Not imported by the default suite.
- `tests/fixtures.py` — fictional mints and a config dict. Not profitability data.
- `tests/test_*.py` — offline unittest modules.
- `.gitignore` — runtime directory and Python caches.

Create files only in the task that owns them. Later tasks import earlier signatures exactly as written here.

Shared signatures produced by the tasks:

```python
SOL_MINT: str
ORDER_URL: str
USDC_MINT: str
SMOKE_AMOUNT: str

class BuyEstimate:
    estimated_fee_lamports: int
    estimated_rent_lamports: int

class DryRunConfig:
    bank_lamports: int
    mint_metadata: dict[str, int]
    denylist_mints: tuple[str, ...] | None
    buy_estimates: dict[str, BuyEstimate]

class Signal:
    id: str
    side: str
    mint: str
    created_at: str
    sort_key: tuple[int, str, int]
    size_lamports: int | None
    token_amount: int | None

class SchemaRejection:
    id: str | None
    log_id: bool
    side: str | None

class QuoteRequest:
    input_mint: str
    output_mint: str
    amount: str

class StoredQuote:
    router: str | None
    in_amount: str
    out_amount: str
    fee_bps: int | None
    fee_mint: str | None
    platform_fee: dict | None

class QuoteSuccess:
    quote: StoredQuote

class QuoteUnsafe:
    pass

class QuoteUnavailable:
    pass

class Receipt:
    id: str
    side: str
    outcome: str
    trigger: str
    mint: str
    quoted_in_amount: str | None
    quoted_out_amount: str | None
    reason: str | None
    denylist: str
    seq: int
    buy_lamports: int | None
    estimated_fee_lamports: int | None
    estimated_rent_lamports: int | None
    quote: StoredQuote | None

class MarkRecord:
    buy_id: str
    n: int
    mark_out_lamports: int
    mark_in_base_units: int
    seq: int
    quote: StoredQuote

class Position:
    buy_id: str
    mint: str
    entry_buy_lamports: int
    entry_out_base_units: int
    token_base_units: int
    high_out_lamports: int
    high_in_base_units: int
    armed: bool

class Book:
    bank_lamports: int
    positions: dict[str, Position]

def parse_size_lamports(value: object) -> int | None: ...
def is_mint(value: object) -> bool: ...
def load_config(path: Path) -> DryRunConfig: ...
def parse_signal(body: object) -> Signal | SchemaRejection: ...
def find_startup_refusal(argv: list[str], env: Mapping[str, str]) -> str | None: ...
def sign(payload: bytes) -> bytes: ...
def receipt_payload(receipt: Receipt) -> dict: ...
def mark_payload(mark: MarkRecord) -> dict: ...
def prequote(config: DryRunConfig, book: Book, signal: Signal) -> str | None: ...
def decide_mark(position: Position, mark_out: int, mark_in: int) -> str: ...
def apply_observation(position: Position, mark_out: int, mark_in: int) -> Position: ...
def order_url(request: QuoteRequest) -> str: ...
def classify(request: QuoteRequest, status: int, body: object) -> QuoteSuccess | QuoteUnsafe | QuoteUnavailable: ...
def quote_once(transport, request: QuoteRequest) -> QuoteSuccess | QuoteUnsafe | QuoteUnavailable: ...
def commit_record(root: Path, relative: Path, payload: dict, *, crash: bool = False) -> None: ...
def discard_temps(root: Path) -> None: ...
def load_records(root: Path) -> tuple[list[Receipt], list[MarkRecord]]: ...
def fold(starting_bank: int, receipts: list[Receipt], marks: list[MarkRecord]) -> Book: ...
def exclusive_lock(path: Path): ...
def run(config: DryRunConfig, inbox: Path, state: Path, transport, *, crash_commits: int = 0, stop: StopFlag | None = None) -> int: ...
def main(argv: list[str] | None = None, environ: Mapping[str, str] | None = None) -> int: ...
```

`decide_mark` returns `"stop"`, `"trailing"`, or `"observe"`. `prequote` returns one of the closed reason strings, or `None`.

---

### Task 1: Package skeleton and runtime ignore rules

**Files:**
- Create: `executor/__init__.py`
- Create: `executor/types.py`
- Create: `tests/fixtures.py`
- Create: `tests/test_types.py`
- Create: `.gitignore`

**Interfaces:**
- Consumes: nothing
- Produces: `SOL_MINT`, `ORDER_URL`, `USDC_MINT`, `SMOKE_AMOUNT`, `BuyEstimate`, `DryRunConfig`, `Signal`, `SchemaRejection`, `QuoteRequest`, `StoredQuote`, `QuoteSuccess`, `QuoteUnsafe`, `QuoteUnavailable`, `Receipt`, `MarkRecord`, `Position`, `Book`, `MINT_A`, `MINT_B`, `MINT_C`, `MINT_D`, `MINT_DENIED`

- [x] **Step 1: Write the failing test**

```python
# tests/test_types.py
import unittest

from executor.types import ORDER_URL, SMOKE_AMOUNT, SOL_MINT, USDC_MINT
from tests.fixtures import MINT_A, MINT_DENIED


class TypesTest(unittest.TestCase):
    def test_constants_match_the_spec(self):
        self.assertEqual(SOL_MINT, "So11111111111111111111111111111111111111112")
        self.assertEqual(ORDER_URL, "https://api.jup.ag/swap/v2/order")
        self.assertEqual(USDC_MINT, "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
        self.assertEqual(SMOKE_AMOUNT, "100000000")
        self.assertNotEqual(MINT_A, MINT_DENIED)


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_types -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'executor'`

- [x] **Step 3: Write minimal implementation**

```python
# executor/__init__.py
```

```python
# executor/types.py
from dataclasses import dataclass

SOL_MINT = "So11111111111111111111111111111111111111112"
ORDER_URL = "https://api.jup.ag/swap/v2/order"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SMOKE_AMOUNT = "100000000"


@dataclass(frozen=True)
class BuyEstimate:
    estimated_fee_lamports: int
    estimated_rent_lamports: int


@dataclass(frozen=True)
class DryRunConfig:
    bank_lamports: int
    mint_metadata: dict[str, int]
    denylist_mints: tuple[str, ...] | None
    buy_estimates: dict[str, BuyEstimate]


@dataclass(frozen=True)
class Signal:
    id: str
    side: str
    mint: str
    created_at: str
    sort_key: tuple[int, str, int]
    size_lamports: int | None
    token_amount: int | None


@dataclass(frozen=True)
class SchemaRejection:
    id: str | None
    log_id: bool
    side: str | None = None


@dataclass(frozen=True)
class QuoteRequest:
    input_mint: str
    output_mint: str
    amount: str


@dataclass(frozen=True)
class StoredQuote:
    router: str | None
    in_amount: str
    out_amount: str
    fee_bps: int | None
    fee_mint: str | None
    platform_fee: dict | None


@dataclass(frozen=True)
class QuoteSuccess:
    quote: StoredQuote


@dataclass(frozen=True)
class QuoteUnsafe:
    pass


@dataclass(frozen=True)
class QuoteUnavailable:
    pass


@dataclass(frozen=True)
class Receipt:
    id: str
    side: str
    outcome: str
    trigger: str
    mint: str
    quoted_in_amount: str | None
    quoted_out_amount: str | None
    reason: str | None
    denylist: str
    seq: int
    buy_lamports: int | None
    estimated_fee_lamports: int | None
    estimated_rent_lamports: int | None
    quote: StoredQuote | None


@dataclass(frozen=True)
class MarkRecord:
    buy_id: str
    n: int
    mark_out_lamports: int
    mark_in_base_units: int
    seq: int
    quote: StoredQuote


@dataclass(frozen=True)
class Position:
    buy_id: str
    mint: str
    entry_buy_lamports: int
    entry_out_base_units: int
    token_base_units: int
    high_out_lamports: int
    high_in_base_units: int
    armed: bool


@dataclass(frozen=True)
class Book:
    bank_lamports: int
    positions: dict[str, Position]
```

```python
# tests/fixtures.py
MINT_A = "8qbHbw2BbbTHBW1sbeqakYXVKRQM8Ne7pLK7m6CVfeR"
MINT_B = "CktRuQ2mttgRGkXJtyksdKHjUdc2C4TgDzyB98oEzy8"
MINT_C = "GgBaCs3NCBuZN12kCJgAW63ydqohFkHEdfdEXBPzLHq"
MINT_D = "LbUiWL3xVV8hTFYBVdbTNrpDo41NKS6o3LHHuDzjfcY"
MINT_DENIED = "cGfHiC6Kgg3FpFZvgwGcswsCRtp4aBP2fzuXRQPizuN"
```

```gitignore
# .gitignore
data/jupiter-dry-runs/
__pycache__/
*.pyc
.venv/
```

- [x] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_types -v`

Expected: PASS, `Ran 1 test`

- [x] **Step 5: Commit**

```bash
git add executor/__init__.py executor/types.py tests/fixtures.py tests/test_types.py .gitignore
git commit -m "feat: add dry-run types and ignore runtime state"
```

### Task 2: Exact integer amounts

**Files:**
- Create: `executor/integers.py`
- Create: `tests/test_integers.py`

**Interfaces:**
- Consumes: nothing
- Produces: `parse_size_lamports(value: object) -> int | None`, `parse_nonneg(value: object) -> int | None`, `parse_pos(value: object) -> int | None`

- [x] **Step 1: Write the failing test**

```python
# tests/test_integers.py
import unittest

from executor.integers import parse_nonneg, parse_pos, parse_size_lamports


class IntegersTest(unittest.TestCase):
    def test_size_sol_converts_without_rounding(self):
        self.assertEqual(parse_size_lamports("0.08"), 80_000_000)
        self.assertEqual(parse_size_lamports("1"), 1_000_000_000)
        self.assertEqual(parse_size_lamports("0.000000001"), 1)
        self.assertIsNone(parse_size_lamports("0.0000000001"))
        self.assertIsNone(parse_size_lamports("0"))
        self.assertIsNone(parse_size_lamports("0.0"))
        self.assertIsNone(parse_size_lamports("01"))
        self.assertIsNone(parse_size_lamports("1e-3"))
        self.assertIsNone(parse_size_lamports("-1"))
        self.assertIsNone(parse_size_lamports(0.08))

    def test_canonical_integers(self):
        self.assertEqual(parse_nonneg("0"), 0)
        self.assertEqual(parse_pos("80000000"), 80_000_000)
        self.assertIsNone(parse_pos("0"))
        self.assertIsNone(parse_nonneg("00"))
        self.assertIsNone(parse_nonneg(5))


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_integers -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'executor.integers'`

- [x] **Step 3: Write minimal implementation**

```python
# executor/integers.py
import re

_NONNEG = re.compile(r"^(0|[1-9][0-9]*)$")
_POS = re.compile(r"^[1-9][0-9]*$")
_SIZE = re.compile(r"^(0|[1-9][0-9]*)(?:\.([0-9]{1,9}))?$")


def parse_nonneg(value: object) -> int | None:
    if not isinstance(value, str) or _NONNEG.fullmatch(value) is None:
        return None
    return int(value)


def parse_pos(value: object) -> int | None:
    if not isinstance(value, str) or _POS.fullmatch(value) is None:
        return None
    return int(value)


def parse_size_lamports(value: object) -> int | None:
    if not isinstance(value, str) or _SIZE.fullmatch(value) is None:
        return None
    whole, frac = value.split(".") if "." in value else (value, "")
    lamports = int(whole) * 1_000_000_000 + int(frac.ljust(9, "0") or "0")
    if lamports <= 0:
        return None
    return lamports
```

- [x] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_integers -v`

Expected: PASS, `Ran 2 tests`

- [x] **Step 5: Commit**

```bash
git add executor/integers.py tests/test_integers.py
git commit -m "feat: parse exact lamport amounts"
```

### Task 3: Config file and mint checks

**Files:**
- Create: `executor/config.py`
- Create: `tests/test_config.py`
- Modify: `tests/fixtures.py`

**Interfaces:**
- Consumes: `BuyEstimate`, `DryRunConfig`, `parse_pos`, `MINT_A`, `MINT_DENIED`
- Produces: `ConfigError`, `is_mint(value: object) -> bool`, `load_config(path: Path) -> DryRunConfig`, `sample_config(**overrides) -> dict`

- [x] **Step 1: Write the failing test**

```python
# tests/test_config.py
import json
import tempfile
import unittest
from pathlib import Path

from executor.config import ConfigError, is_mint, load_config
from executor.types import SOL_MINT
from tests.fixtures import MINT_A, MINT_DENIED, sample_config


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def write(self, body: dict) -> Path:
        path = self.dir / "config.json"
        path.write_text(json.dumps(body), encoding="utf-8")
        return path

    def test_fixture_mints_are_32_bytes(self):
        self.assertTrue(is_mint(MINT_A))
        self.assertTrue(is_mint(MINT_DENIED))
        self.assertTrue(is_mint(SOL_MINT))
        self.assertFalse(is_mint("ECR"))

    def test_absent_denylist_and_estimates_have_distinct_empty_states(self):
        loaded = load_config(self.write(sample_config()))
        self.assertEqual(loaded.bank_lamports, 1_000_000_000)
        self.assertIsNone(loaded.denylist_mints)
        self.assertEqual(loaded.buy_estimates, {})
        self.assertEqual(loaded.mint_metadata[MINT_A], 6)

    def test_empty_denylist_is_configured(self):
        loaded = load_config(self.write(sample_config(denylist_mints=[])))
        self.assertEqual(loaded.denylist_mints, ())

    def test_unknown_key_bad_bank_and_symbol_denylist_are_config_errors(self):
        for body in (
            sample_config(mode="live"),
            sample_config(bank_lamports="0"),
            sample_config(bank_lamports=100),
            sample_config(denylist_mints=["ECR"]),
            sample_config(mint_metadata=[{"mint": MINT_A, "decimals": True}]),
            sample_config(mint_metadata=[{"mint": MINT_A, "decimals": 256}]),
            sample_config(buy_estimates={"buy1": {"estimated_fee_lamports": "1"}}),
        ):
            with self.assertRaises(ConfigError):
                load_config(self.write(body))


if __name__ == "__main__":
    unittest.main()
```

Append `sample_config` to `tests/fixtures.py` in Step 3, but the failing run in Step 2 is expected to miss `executor.config` first. Add the helper before Step 2 so the import error is the config module:

```python
# append to tests/fixtures.py
def sample_config(**overrides) -> dict:
    body = {
        "bank_lamports": "1000000000",
        "mint_metadata": [
            {"mint": MINT_A, "decimals": 6},
            {"mint": MINT_B, "decimals": 6},
            {"mint": MINT_C, "decimals": 6},
            {"mint": MINT_D, "decimals": 6},
        ],
    }
    body.update(overrides)
    return body
```

- [x] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_config -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'executor.config'`

- [x] **Step 3: Write minimal implementation**

```python
# executor/config.py
import json
from pathlib import Path

from executor.integers import parse_nonneg, parse_pos
from executor.types import BuyEstimate, DryRunConfig

_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_CLOSED = {"bank_lamports", "mint_metadata", "denylist_mints", "buy_estimates"}


class ConfigError(Exception):
    pass


def is_mint(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if any(char not in _ALPHABET for char in value):
        return False
    number = 0
    for char in value:
        number = number * 58 + _ALPHABET.index(char)
    pad = 0
    for char in value:
        if char != "1":
            break
        pad += 1
    raw = b"" if number == 0 else number.to_bytes((number.bit_length() + 7) // 8, "big")
    return len(b"\x00" * pad + raw) == 32


def load_config(path: Path) -> DryRunConfig:
    body = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(body, dict) or not set(body).issubset(_CLOSED):
        raise ConfigError("refused: config")
    if "bank_lamports" not in body:
        raise ConfigError("refused: config")
    bank = parse_pos(body["bank_lamports"])
    if bank is None:
        raise ConfigError("refused: config")
    metadata: dict[str, int] = {}
    for item in body.get("mint_metadata", []):
        if not isinstance(item, dict) or set(item) != {"mint", "decimals"}:
            raise ConfigError("refused: config")
        decimals = item["decimals"]
        if isinstance(decimals, bool) or not isinstance(decimals, int) or not 0 <= decimals <= 255:
            raise ConfigError("refused: config")
        if not is_mint(item["mint"]) or item["mint"] in metadata:
            raise ConfigError("refused: config")
        metadata[item["mint"]] = decimals
    denylist: tuple[str, ...] | None = None
    if "denylist_mints" in body:
        raw = body["denylist_mints"]
        if not isinstance(raw, list) or not all(is_mint(item) for item in raw):
            raise ConfigError("refused: config")
        denylist = tuple(raw)
    estimates: dict[str, BuyEstimate] = {}
    raw_estimates = body.get("buy_estimates", {})
    if not isinstance(raw_estimates, dict):
        raise ConfigError("refused: config")
    for signal_id, item in raw_estimates.items():
        if not isinstance(item, dict) or set(item) != {
            "estimated_fee_lamports",
            "estimated_rent_lamports",
        }:
            raise ConfigError("refused: config")
        fee = parse_nonneg(item["estimated_fee_lamports"])
        rent = parse_nonneg(item["estimated_rent_lamports"])
        if fee is None or rent is None:
            raise ConfigError("refused: config")
        estimates[signal_id] = BuyEstimate(fee, rent)
    return DryRunConfig(bank, metadata, denylist, estimates)
```

- [x] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_config -v`

Expected: PASS, `Ran 4 tests`

- [x] **Step 5: Commit**

```bash
git add executor/config.py tests/test_config.py tests/fixtures.py
git commit -m "feat: load the closed dry-run config"
```

### Task 4: Inbox signal schema

**Files:**
- Create: `executor/schema.py`
- Create: `tests/test_schema.py`

**Interfaces:**
- Consumes: `SchemaRejection`, `Signal`, `is_mint`, `parse_pos`, `parse_size_lamports`, `MINT_A`
- Produces: `parse_signal(body: object) -> Signal | SchemaRejection`, `log_schema(rejection: SchemaRejection) -> None`

- [x] **Step 1: Write the failing test**

```python
# tests/test_schema.py
import io
import unittest
from contextlib import redirect_stderr

from executor.schema import log_schema, parse_signal
from executor.types import SchemaRejection, Signal
from tests.fixtures import MINT_A


def buy(**extra):
    body = {
        "id": "buy1",
        "side": "buy",
        "mint": MINT_A,
        "created_at": "2026-11-07T10:00:00+02:00",
        "size_sol": "0.08",
    }
    body.update(extra)
    return body


class SchemaTest(unittest.TestCase):
    def test_buy_and_sell_field_sets(self):
        parsed = parse_signal(buy())
        self.assertIsInstance(parsed, Signal)
        self.assertEqual(parsed.size_lamports, 80_000_000)
        self.assertIsNone(parsed.token_amount)
        sell = parse_signal(
            {
                "id": "sell1",
                "side": "sell",
                "mint": MINT_A,
                "created_at": "2026-11-07T10:01:00Z",
                "token_amount": "1000",
            }
        )
        self.assertIsInstance(sell, Signal)
        self.assertEqual(sell.token_amount, 1000)
        self.assertLess(parsed.sort_key, sell.sort_key)

    def test_rejects_unknown_fields_zero_and_unsafe_ids_without_logging_the_body(self):
        cases = [
            buy(note="secret-body"),
            buy(size_sol="0"),
            buy(token_amount="1"),
            buy(id="rexit.buy1.stop"),
            buy(created_at="2026-11-07T10:00:00"),
            {"id": "bad id", "side": "buy"},
        ]
        for body in cases:
            parsed = parse_signal(body)
            self.assertIsInstance(parsed, SchemaRejection)
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            log_schema(parse_signal(buy(note="secret-body")))
        self.assertNotIn("secret-body", stderr.getvalue())
        self.assertIn("schema buy1", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_schema -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'executor.schema'`

- [x] **Step 3: Write minimal implementation**

```python
# executor/schema.py
import re
import sys
from datetime import datetime, timezone

from executor.config import is_mint
from executor.integers import parse_pos, parse_size_lamports
from executor.types import SchemaRejection, Signal

_SIGNAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_BUY = {"id", "side", "mint", "created_at", "size_sol"}
_SELL = {"id", "side", "mint", "created_at", "token_amount"}


def _safe_id(value: object) -> str | None:
    if isinstance(value, str) and _SIGNAL_ID.fullmatch(value) and not value.startswith("rexit."):
        return value
    return None


def _millis(value: str) -> int | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    delta = parsed.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return ((delta.days * 86400 + delta.seconds) * 1000) + (delta.microseconds // 1000)


def log_schema(rejection: SchemaRejection) -> None:
    if rejection.log_id and rejection.id is not None:
        print(f"schema {rejection.id}", file=sys.stderr)
    else:
        print("schema", file=sys.stderr)


def parse_signal(body: object) -> Signal | SchemaRejection:
    if not isinstance(body, dict):
        return SchemaRejection(None, False, None)
    signal_id = _safe_id(body.get("id"))
    side = body.get("side")
    known_side = side if side in {"buy", "sell"} else None
    allowed = _BUY if side == "buy" else _SELL if side == "sell" else None
    if allowed is None or set(body) != allowed or signal_id is None or not is_mint(body.get("mint")):
        return SchemaRejection(signal_id, signal_id is not None, known_side)
    millis = _millis(body.get("created_at")) if isinstance(body.get("created_at"), str) else None
    if millis is None:
        return SchemaRejection(signal_id, True, known_side)
    if side == "buy":
        lamports = parse_size_lamports(body.get("size_sol"))
        if lamports is None:
            return SchemaRejection(signal_id, True, known_side)
        amount = None
    else:
        amount = parse_pos(body.get("token_amount"))
        if amount is None:
            return SchemaRejection(signal_id, True, known_side)
        lamports = None
    return Signal(
        signal_id,
        side,
        body["mint"],
        body["created_at"],
        (millis, signal_id, 0 if side == "buy" else 1),
        lamports,
        amount,
    )
```

- [x] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_schema -v`

Expected: PASS, `Ran 2 tests`

- [x] **Step 5: Commit**

```bash
git add executor/schema.py tests/test_schema.py
git commit -m "feat: validate inbox signal schema"
```

### Task 5: Startup refusal

**Files:**
- Create: `executor/startup.py`
- Create: `tests/test_startup.py`

**Interfaces:**
- Consumes: nothing from the config loader
- Produces: `find_startup_refusal(argv: list[str], env: Mapping[str, str]) -> str | None`

- [x] **Step 1: Write the failing test**

```python
# tests/test_startup.py
import unittest

from executor.startup import find_startup_refusal


class StartupTest(unittest.TestCase):
    def test_live_and_mandate_refuse_without_echoing_secrets(self):
        secret = "super-secret-value"
        cases = [
            (["--live"], {}, "refused: live"),
            ([], {"MODE": "live"}, "refused: live"),
            ([], {"LIVE": "1"}, "refused: live"),
            ([], {"DRY_RUN": "0"}, "refused: live"),
            ([], {"DRY_RUN": "false"}, "refused: live"),
            (["--mandate", "later.json"], {}, "refused: mandate"),
            ([], {"MANDATE_EXPIRY": "2026-11-08T00:00:00+02:00"}, "refused: mandate"),
            ([], {"JUPITER_API_KEY": secret}, "refused: live"),
            ([], {"TAKER": "wallet"}, "refused: live"),
            ([], {"EXECUTE_URL": "https://api.jup.ag/swap/v2/execute"}, "refused: live"),
            (["--build-url", "https://api.jup.ag/swap/v2/build"], {}, "refused: live"),
            (["--mystery"], {}, "refused: config"),
        ]
        for argv, env, expected in cases:
            message = find_startup_refusal(argv, env)
            self.assertEqual(message, expected)
            self.assertNotIn(secret, message)

    def test_dry_run_invocation_has_no_refusal(self):
        argv = ["--config", "cfg.json", "--inbox", "inbox", "--state", "state"]
        self.assertIsNone(find_startup_refusal(argv, {}))
        self.assertIsNone(find_startup_refusal(argv, {"DRY_RUN": "1", "MODE": "dry_run"}))


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_startup -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'executor.startup'`

- [x] **Step 3: Write minimal implementation**

```python
# executor/startup.py
from collections.abc import Mapping

_LIVE_FLAGS = {
    "--live",
    "--taker",
    "--payer",
    "--referral-account",
    "--api-key",
    "--execute-url",
    "--build-url",
    "--signer",
    "--rpc-url",
    "--webhook-url",
    "--private-key",
}
_MANDATE_FLAGS = {"--mandate", "--mandate-expiry"}
_ALLOWED = {"--config", "--inbox", "--state"}
_SECRET_ENV = {
    "TAKER",
    "FEE_PAYER",
    "PAYER",
    "REFERRAL_ACCOUNT",
    "REFERRAL_FEE",
    "JUPITER_API_KEY",
    "X_API_KEY",
    "API_KEY",
    "EXECUTE_URL",
    "BUILD_URL",
    "SIGNER",
    "WALLET_SECRET",
    "PRIVATE_KEY",
    "SECRET_KEY",
    "RPC_URL",
    "HELIUS_URL",
    "WEBHOOK_URL",
    "WEBHOOK_AUTH",
}


def find_startup_refusal(argv: list[str], env: Mapping[str, str]) -> str | None:
    names = set(argv)
    if names & _LIVE_FLAGS or any(item.startswith("--live=") for item in argv):
        return "refused: live"
    if names & _MANDATE_FLAGS or env.get("MANDATE_FILE") or env.get("MANDATE_EXPIRY"):
        return "refused: mandate"
    if env.get("MODE") == "live" or env.get("LIVE") == "1" or env.get("DRY_RUN") in {"0", "false"}:
        return "refused: live"
    if any(env.get(name) for name in _SECRET_ENV):
        return "refused: live"
    index = 0
    while index < len(argv):
        flag = argv[index]
        if flag not in _ALLOWED:
            return "refused: config"
        if index + 1 >= len(argv) or argv[index + 1].startswith("--"):
            return "refused: config"
        index += 2
    return None
```

- [x] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_startup -v`

Expected: PASS, `Ran 2 tests`

- [x] **Step 5: Commit**

```bash
git add executor/startup.py tests/test_startup.py
git commit -m "feat: refuse live and mandate startup"
```

### Task 6: Signer refusal

**Files:**
- Create: `executor/signer.py`
- Create: `tests/test_signer.py`

**Interfaces:**
- Consumes: nothing
- Produces: `SignerRefusal`, `sign(payload: bytes) -> bytes`

- [x] **Step 1: Write the failing test**

```python
# tests/test_signer.py
import unittest

from executor.signer import SignerRefusal, sign


class SignerTest(unittest.TestCase):
    def test_sign_always_raises_and_returns_nothing(self):
        with self.assertRaises(SignerRefusal):
            sign(b"transaction")


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_signer -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'executor.signer'`

- [x] **Step 3: Write minimal implementation**

```python
# executor/signer.py
class SignerRefusal(Exception):
    pass


def sign(payload: bytes) -> bytes:
    raise SignerRefusal("signer refused")
```

- [x] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_signer -v`

Expected: PASS, `Ran 1 test`

- [x] **Step 5: Commit**

```bash
git add executor/signer.py tests/test_signer.py
git commit -m "feat: refuse every signer call"
```

### Task 7: Dry-run receipt and mark records

**Files:**
- Create: `executor/records.py`
- Create: `tests/test_records.py`

**Interfaces:**
- Consumes: `MarkRecord`, `Receipt`, `StoredQuote`
- Produces: `receipt_payload(receipt: Receipt) -> dict`, `mark_payload(mark: MarkRecord) -> dict`

- [x] **Step 1: Write the failing test**

```python
# tests/test_records.py
import unittest

from executor.records import mark_payload, receipt_payload
from executor.types import MarkRecord, Receipt, StoredQuote
from tests.fixtures import MINT_A


def quote() -> StoredQuote:
    return StoredQuote("metis", "80000000", "1000", 10, None, None)


class RecordsTest(unittest.TestCase):
    def test_receipt_status_is_dry_run_and_has_no_fill_fields(self):
        payload = receipt_payload(
            Receipt(
                "buy1",
                "buy",
                "quoted",
                "signal",
                MINT_A,
                "80000000",
                "1000",
                None,
                "not_configured",
                1,
                80_000_000,
                5000,
                0,
                quote(),
            )
        )
        self.assertEqual(payload["record_type"], "jupiter_dry_run_receipt")
        self.assertEqual(payload["status"], "dry_run")
        self.assertEqual(payload["seq"], "1")
        self.assertEqual(payload["quote"]["inAmount"], "80000000")
        forbidden = {"signature", "signed", "submitted", "filled", "transaction", "requestId"}
        self.assertTrue(forbidden.isdisjoint(payload))

    def test_rejected_receipt_nulls_quote_amounts(self):
        payload = receipt_payload(
            Receipt("buy1", "buy", "rejected", "signal", MINT_A, None, None, "risk_cap", "configured", 2, None, None, None, None)
        )
        self.assertEqual(payload["reason"], "risk_cap")
        self.assertIsNone(payload["quoted_in_amount"])
        self.assertIsNone(payload["quote"])

    def test_mark_payload(self):
        payload = mark_payload(MarkRecord("buy1", 1, 112_000_000, 1_000_000_000, 3, quote()))
        self.assertEqual(payload["record_type"], "jupiter_dry_run_mark")
        self.assertEqual(payload["n"], "1")
        self.assertEqual(payload["mark_out_lamports"], "112000000")
        self.assertNotIn("status", payload)


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_records -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'executor.records'`

- [x] **Step 3: Write minimal implementation**

```python
# executor/records.py
from executor.types import MarkRecord, Receipt, StoredQuote


def _quote(quote: StoredQuote | None) -> dict | None:
    if quote is None:
        return None
    return {
        "router": quote.router,
        "inAmount": quote.in_amount,
        "outAmount": quote.out_amount,
        "feeBps": quote.fee_bps,
        "feeMint": quote.fee_mint,
        "platformFee": quote.platform_fee,
    }


def _canon(value: int | None) -> str | None:
    if value is None:
        return None
    return str(value)


def receipt_payload(receipt: Receipt) -> dict:
    return {
        "record_type": "jupiter_dry_run_receipt",
        "id": receipt.id,
        "side": receipt.side,
        "status": "dry_run",
        "outcome": receipt.outcome,
        "trigger": receipt.trigger,
        "mint": receipt.mint,
        "quoted_in_amount": receipt.quoted_in_amount,
        "quoted_out_amount": receipt.quoted_out_amount,
        "reason": receipt.reason,
        "denylist": receipt.denylist,
        "seq": str(receipt.seq),
        "buy_lamports": _canon(receipt.buy_lamports),
        "estimated_fee_lamports": _canon(receipt.estimated_fee_lamports),
        "estimated_rent_lamports": _canon(receipt.estimated_rent_lamports),
        "quote": _quote(receipt.quote),
    }


def mark_payload(mark: MarkRecord) -> dict:
    return {
        "record_type": "jupiter_dry_run_mark",
        "buy_id": mark.buy_id,
        "n": str(mark.n),
        "mark_out_lamports": str(mark.mark_out_lamports),
        "mark_in_base_units": str(mark.mark_in_base_units),
        "seq": str(mark.seq),
        "quote": _quote(mark.quote),
    }
```

- [x] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_records -v`

Expected: PASS, `Ran 3 tests`

- [x] **Step 5: Commit**

```bash
git add executor/records.py tests/test_records.py
git commit -m "feat: serialize dry-run receipts and marks"
```

### Task 8: Pre-quote risk

**Files:**
- Create: `executor/risk.py`
- Create: `tests/test_risk.py`

**Interfaces:**
- Consumes: `Book`, `BuyEstimate`, `DryRunConfig`, `Position`, `Signal`, `MINT_A`, `MINT_B`, `MINT_C`, `MINT_D`, `MINT_DENIED`
- Produces: `prequote(config: DryRunConfig, book: Book, signal: Signal) -> str | None`, `denylist_label(config: DryRunConfig) -> str`

- [x] **Step 1: Write the failing test**

```python
# tests/test_risk.py
import unittest

from executor.risk import denylist_label, prequote
from executor.types import Book, BuyEstimate, DryRunConfig, Position, Signal
from executor.types import SOL_MINT
from tests.fixtures import MINT_A, MINT_B, MINT_C, MINT_D, MINT_DENIED


def config(bank=1_000_000_000, denylist=None, estimates=None, mints=None):
    return DryRunConfig(
        bank,
        mints if mints is not None else {MINT_A: 6, MINT_B: 6, MINT_C: 6, MINT_D: 6},
        denylist,
        estimates if estimates is not None else {"buy1": BuyEstimate(5000, 0)},
    )


def buy(mint=MINT_A, lamports=80_000_000, signal_id="buy1"):
    return Signal(signal_id, "buy", mint, "t", (0, signal_id, 0), lamports, None)


def sell(amount=1000, mint=MINT_A, signal_id="sell1"):
    return Signal(signal_id, "sell", mint, "t", (1, signal_id, 1), None, amount)


def position(mint=MINT_A):
    return Position("buy1", mint, 80_000_000, 1000, 1000, 80_000_000, 1000, False)


class RiskTest(unittest.TestCase):
    def test_cap_and_reserve_examples(self):
        book = Book(1_000_000_000, {})
        self.assertIsNone(prequote(config(), book, buy()))
        self.assertEqual(prequote(config(), book, buy(lamports=80_000_001)), "risk_cap")
        small = Book(40_000_000, {})
        self.assertEqual(
            prequote(config(40_000_000, estimates={"buy1": BuyEstimate(7_000_000, 0)}), small, buy(lamports=3_200_000)),
            "risk_reserve",
        )
        self.assertIsNone(
            prequote(config(40_000_000, estimates={"buy1": BuyEstimate(6_800_000, 0)}), small, buy(lamports=3_200_000))
        )
        self.assertEqual(prequote(config(estimates={}), book, buy()), "risk_reserve")

    def test_positions_sells_denylist_and_unknown_mint(self):
        positions = {mint: position(mint) for mint in (MINT_A, MINT_B, MINT_C)}
        book = Book(1_000_000_000, positions)
        self.assertEqual(prequote(config(), book, buy(MINT_D, signal_id="buy4")), "risk_max_positions")
        self.assertEqual(prequote(config(), book, buy(MINT_A)), "risk_mint_open")
        self.assertEqual(prequote(config(), Book(1, {}), sell()), "risk_no_position")
        self.assertEqual(prequote(config(), Book(1, {MINT_A: position()}), sell(amount=999)), "risk_sell_size")
        self.assertIsNone(prequote(config(), Book(1, {MINT_A: position()}), sell(amount=1000)))
        denied = config(denylist=(MINT_DENIED,))
        self.assertEqual(prequote(denied, Book(denied.bank_lamports, {}), buy(MINT_DENIED)), "denylist")
        self.assertEqual(denylist_label(config()), "not_configured")
        self.assertEqual(denylist_label(config(denylist=())), "configured")
        self.assertEqual(prequote(config(), Book(1_000_000_000, {}), buy("not-a-listed-mint")), "unknown_mint")
        self.assertEqual(prequote(config(), Book(1, {}), buy(SOL_MINT)), "pair")

    def test_decimals_do_not_scale_base_units(self):
        book = Book(1_000_000_000, {MINT_A: position()})
        self.assertIsNone(prequote(config(mints={MINT_A: 0}), book, sell(amount=1000)))
        self.assertIsNone(prequote(config(mints={MINT_A: 9}), book, sell(amount=1000)))
        self.assertEqual(
            prequote(config(mints={MINT_A: 0}), book, sell(amount=999)),
            "risk_sell_size",
        )


if __name__ == "__main__":
    unittest.main()
```

The `buy(SOL_MINT)` call uses the mint as the first positional argument. `unknown_mint` for an unlisted mint is asserted before that line. `pair` is checked before metadata, so `SOL_MINT` does not need to be in `mint_metadata`.

- [x] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_risk -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'executor.risk'`

- [x] **Step 3: Write minimal implementation**

```python
# executor/risk.py
from executor.types import SOL_MINT, Book, DryRunConfig, Signal


def denylist_label(config: DryRunConfig) -> str:
    if config.denylist_mints is None:
        return "not_configured"
    return "configured"


def prequote(config: DryRunConfig, book: Book, signal: Signal) -> str | None:
    if signal.mint == SOL_MINT:
        return "pair"
    if signal.mint not in config.mint_metadata:
        return "unknown_mint"
    if config.denylist_mints is not None and signal.mint in config.denylist_mints:
        return "denylist"
    if signal.side == "buy":
        if signal.id not in config.buy_estimates:
            return "risk_reserve"
        if signal.mint in book.positions:
            return "risk_mint_open"
        if len(book.positions) >= 3:
            return "risk_max_positions"
        assert signal.size_lamports is not None
        if signal.size_lamports * 100 > book.bank_lamports * 8:
            return "risk_cap"
        estimate = config.buy_estimates[signal.id]
        post = book.bank_lamports - signal.size_lamports - estimate.estimated_fee_lamports - estimate.estimated_rent_lamports
        if post < 30_000_000:
            return "risk_reserve"
        return None
    if signal.mint not in book.positions:
        return "risk_no_position"
    assert signal.token_amount is not None
    if signal.token_amount != book.positions[signal.mint].token_base_units:
        return "risk_sell_size"
    return None
```

- [x] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_risk -v`

Expected: PASS, `Ran 3 tests`

- [x] **Step 5: Commit**

```bash
git add executor/risk.py tests/test_risk.py
git commit -m "feat: apply pre-quote risk checks"
```

### Task 9: Stop and trailing decisions

**Files:**
- Create: `executor/marks.py`
- Create: `tests/test_marks.py`

**Interfaces:**
- Consumes: `Position`, `MINT_A`
- Produces: `decide_mark(position: Position, mark_out: int, mark_in: int) -> str`, `apply_observation(position: Position, mark_out: int, mark_in: int) -> Position`

- [x] **Step 1: Write the failing test**

```python
# tests/test_marks.py
import unittest
from dataclasses import replace

from executor.marks import apply_observation, decide_mark
from executor.types import Position
from tests.fixtures import MINT_A


def open_position():
    return Position("buy1", MINT_A, 100_000_000, 1_000_000_000, 1_000_000_000, 100_000_000, 1_000_000_000, False)


class MarksTest(unittest.TestCase):
    def test_thresholds_use_exact_cross_multiplication(self):
        position = open_position()
        self.assertEqual(decide_mark(position, 94_000_000, 1_000_000_000), "stop")
        self.assertEqual(decide_mark(position, 94_000_001, 1_000_000_000), "observe")
        armed = apply_observation(position, 112_000_000, 1_000_000_000)
        self.assertTrue(armed.armed)
        self.assertEqual(armed.high_out_lamports, 112_000_000)
        peak = apply_observation(armed, 120_000_000, 1_000_000_000)
        self.assertEqual(decide_mark(peak, 112_800_000, 1_000_000_000), "trailing")
        self.assertEqual(decide_mark(peak, 112_800_001, 1_000_000_000), "observe")
        crashed = replace(peak, armed=True)
        self.assertEqual(decide_mark(crashed, 94_000_000, 1_000_000_000), "stop")
        midway = apply_observation(position, 110_000_000, 1_000_000_000)
        self.assertFalse(midway.armed)
        self.assertEqual(decide_mark(midway, 110_000_000, 1_000_000_000), "observe")


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_marks -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'executor.marks'`

- [x] **Step 3: Write minimal implementation**

```python
# executor/marks.py
from dataclasses import replace

from executor.types import Position


def decide_mark(position: Position, mark_out: int, mark_in: int) -> str:
    if mark_out * position.entry_out_base_units * 100 <= position.entry_buy_lamports * mark_in * 94:
        return "stop"
    if position.armed and mark_out * position.high_in_base_units * 100 <= position.high_out_lamports * mark_in * 94:
        return "trailing"
    return "observe"


def apply_observation(position: Position, mark_out: int, mark_in: int) -> Position:
    high_out = position.high_out_lamports
    high_in = position.high_in_base_units
    if mark_out * high_in > high_out * mark_in:
        high_out = mark_out
        high_in = mark_in
    armed = position.armed or (
        mark_out * position.entry_out_base_units * 100 >= position.entry_buy_lamports * mark_in * 112
    )
    return replace(position, high_out_lamports=high_out, high_in_base_units=high_in, armed=armed)
```

- [x] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_marks -v`

Expected: PASS, `Ran 1 test`

- [x] **Step 5: Commit**

```bash
git add executor/marks.py tests/test_marks.py
git commit -m "feat: decide stop and trailing observations"
```

### Task 10: Quote-only adapter

**Files:**
- Create: `executor/quote.py`
- Create: `tests/test_quote.py`

**Interfaces:**
- Consumes: `QuoteRequest`, `QuoteSuccess`, `QuoteUnavailable`, `QuoteUnsafe`, `StoredQuote`, `ORDER_URL`, `SOL_MINT`, `MINT_A`
- Produces: `order_url(request: QuoteRequest) -> str`, `classify(request: QuoteRequest, status: int, body: object) -> QuoteSuccess | QuoteUnsafe | QuoteUnavailable`, `quote_once(transport, request: QuoteRequest) -> QuoteSuccess | QuoteUnsafe | QuoteUnavailable`, `UrllibTransport`

A transport `get` returns `(status: int, body: object)` and records the URL and headers it was given. `quote_once` passes `headers={}`.

- [x] **Step 1: Write the failing test**

```python
# tests/test_quote.py
import unittest

from executor.quote import UrllibTransport, classify, order_url, quote_once
from executor.types import ORDER_URL, QuoteRequest, QuoteSuccess, QuoteUnavailable, QuoteUnsafe, SOL_MINT
from tests.fixtures import MINT_A


def request():
    return QuoteRequest(SOL_MINT, MINT_A, "80000000")


def ok_body(**extra):
    body = {
        "router": "metis",
        "inAmount": "80000000",
        "outAmount": "1000",
        "feeBps": 10,
        "feeMint": MINT_A,
        "platformFee": {"amount": "1", "feeBps": 10, "feeMint": MINT_A, "ignored": True},
        "transaction": None,
        "requestId": "do-not-store",
        "inUsdValue": 12,
        "routePlan": [{"drop": True}],
    }
    body.update(extra)
    return body


class FakeTransport:
    def __init__(self, status, body, error=None):
        self.status = status
        self.body = body
        self.error = error
        self.calls = []

    def get(self, url, headers):
        self.calls.append((url, dict(headers)))
        if self.error:
            raise self.error
        return self.status, self.body


class QuoteTest(unittest.TestCase):
    def test_url_has_three_params_and_no_taker(self):
        url = order_url(request())
        self.assertTrue(url.startswith(ORDER_URL + "?"))
        self.assertIn("inputMint=" + SOL_MINT, url)
        self.assertIn("outputMint=" + MINT_A, url)
        self.assertIn("amount=80000000", url)
        self.assertNotIn("taker", url)
        self.assertNotIn("execute", url)
        self.assertNotIn("api-key", url)

    def test_success_strips_transaction_and_usd(self):
        result = classify(request(), 200, ok_body())
        self.assertIsInstance(result, QuoteSuccess)
        self.assertEqual(result.quote.out_amount, "1000")
        self.assertEqual(result.quote.platform_fee, {"amount": "1", "feeBps": 10, "feeMint": MINT_A})
        self.assertNotIn("requestId", result.quote.__dict__)

    def test_transaction_string_is_unsafe_and_transport_errors_commit_nothing(self):
        self.assertIsInstance(classify(request(), 200, ok_body(transaction="")), QuoteUnsafe)
        self.assertIsInstance(classify(request(), 200, ok_body(transaction="AQID")), QuoteUnsafe)
        self.assertIsInstance(classify(request(), 200, ok_body(inAmount="1")), QuoteUnavailable)
        self.assertIsInstance(classify(request(), 429, ok_body()), QuoteUnavailable)
        fake = FakeTransport(200, ok_body(), TimeoutError("slow"))
        self.assertIsInstance(quote_once(fake, request()), QuoteUnavailable)
        self.assertEqual(fake.calls[0][1], {})

    def test_urllib_transport_builds_a_get_without_api_key(self):
        captured = {}

        class Response:
            status = 200

            def read(self):
                return b'{"transaction":null,"inAmount":"80000000","outAmount":"1000"}'

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_urlopen(req, timeout=0):
            captured["url"] = req.full_url
            captured["headers"] = {key.lower(): value for key, value in req.header_items()}
            return Response()

        import executor.quote as quote_mod

        original = quote_mod.urlopen
        quote_mod.urlopen = fake_urlopen
        try:
            status, body = UrllibTransport().get(order_url(request()), {})
        finally:
            quote_mod.urlopen = original
        self.assertEqual(status, 200)
        self.assertEqual(body["outAmount"], "1000")
        self.assertNotIn("taker", captured["url"])
        self.assertNotIn("x-api-key", captured["headers"])


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_quote -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'executor.quote'`

- [x] **Step 3: Write minimal implementation**

```python
# executor/quote.py
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from executor.integers import parse_pos
from executor.types import ORDER_URL, QuoteRequest, QuoteSuccess, QuoteUnavailable, QuoteUnsafe, StoredQuote


def order_url(request: QuoteRequest) -> str:
    query = urlencode(
        (
            ("inputMint", request.input_mint),
            ("outputMint", request.output_mint),
            ("amount", request.amount),
        )
    )
    return ORDER_URL + "?" + query


def _platform_fee(body: dict) -> dict | None:
    raw = body.get("platformFee")
    if not isinstance(raw, dict):
        return None
    kept = {}
    if isinstance(raw.get("amount"), str):
        kept["amount"] = raw["amount"]
    fee_bps = raw.get("feeBps")
    if isinstance(fee_bps, int) and not isinstance(fee_bps, bool):
        kept["feeBps"] = fee_bps
    if isinstance(raw.get("feeMint"), str):
        kept["feeMint"] = raw["feeMint"]
    return kept or None


def classify(request: QuoteRequest, status: int, body: object):
    if isinstance(body, dict) and "transaction" in body and body["transaction"] is not None:
        return QuoteUnsafe()
    if status != 200 or not isinstance(body, dict) or body.get("transaction") is not None:
        return QuoteUnavailable()
    if body.get("inAmount") != request.amount or parse_pos(body.get("outAmount")) is None:
        return QuoteUnavailable()
    for key, expected in (("inputMint", request.input_mint), ("outputMint", request.output_mint)):
        if key in body and body[key] != expected:
            return QuoteUnavailable()
    fee_bps = body.get("feeBps")
    if isinstance(fee_bps, bool) or not isinstance(fee_bps, int):
        fee_bps = None
    fee_mint = body.get("feeMint") if isinstance(body.get("feeMint"), str) else None
    router = body.get("router") if isinstance(body.get("router"), str) else None
    return QuoteSuccess(StoredQuote(router, body["inAmount"], body["outAmount"], fee_bps, fee_mint, _platform_fee(body)))


def quote_once(transport, request: QuoteRequest):
    try:
        status, body = transport.get(order_url(request), {})
    except (TimeoutError, OSError):
        return QuoteUnavailable()
    return classify(request, status, body)


class UrllibTransport:
    def get(self, url, headers):
        req = Request(url, headers=dict(headers), method="GET")
        with urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
```

- [x] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_quote -v`

Expected: PASS, `Ran 4 tests`

- [x] **Step 5: Commit**

```bash
git add executor/quote.py tests/test_quote.py
git commit -m "feat: add the quote-only Jupiter adapter"
```

### Task 11: Durable journal

**Files:**
- Create: `executor/journal.py`
- Create: `tests/test_journal.py`

**Interfaces:**
- Consumes: `receipt_payload`, `Receipt`, `StoredQuote`, `MINT_A`
- Produces: `CrashBeforeRename`, `commit_record(root: Path, relative: Path, payload: dict, *, crash: bool = False) -> None`, `discard_temps(root: Path) -> None`, `load_records(root: Path) -> tuple[list[Receipt], list[MarkRecord]]`, `receipt_path(signal_id: str, side: str) -> Path`, `mark_path(buy_id: str, n: int) -> Path`

- [x] **Step 1: Write the failing test**

```python
# tests/test_journal.py
import tempfile
import unittest
from pathlib import Path

from executor.journal import CrashBeforeRename, commit_record, discard_temps, load_records, receipt_path
from executor.records import receipt_payload
from executor.types import Receipt, StoredQuote
from tests.fixtures import MINT_A


def payload(seq=1):
    return receipt_payload(
        Receipt(
            "buy1",
            "buy",
            "quoted",
            "signal",
            MINT_A,
            "80000000",
            "1000",
            None,
            "not_configured",
            seq,
            80_000_000,
            5000,
            0,
            StoredQuote("metis", "80000000", "1000", None, None, None),
        )
    )


class JournalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_rename_is_durable_and_temp_without_rename_is_discarded(self):
        relative = receipt_path("buy1", "buy")
        with self.assertRaises(CrashBeforeRename):
            commit_record(self.root, relative, payload(), crash=True)
        self.assertFalse((self.root / relative).exists())
        self.assertTrue(list(self.root.rglob("*.tmp")))
        discard_temps(self.root)
        self.assertFalse(list(self.root.rglob("*.tmp")))
        commit_record(self.root, relative, payload())
        loaded, marks = load_records(self.root)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].seq, 1)
        self.assertEqual(marks, [])
        self.assertFalse(list(self.root.rglob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_journal -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'executor.journal'`

- [x] **Step 3: Write minimal implementation**

```python
# executor/journal.py
import json
import os
import re
from pathlib import Path

from executor.integers import parse_nonneg, parse_pos
from executor.types import MarkRecord, Receipt, StoredQuote

_SIGNAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_EXIT_ID = re.compile(r"^rexit\.[A-Za-z0-9][A-Za-z0-9._-]{0,95}\.(stop|trailing)$")


class CrashBeforeRename(Exception):
    pass


class JournalError(Exception):
    pass


def _journal_id(value: str) -> bool:
    if _EXIT_ID.fullmatch(value):
        return True
    return _SIGNAL_ID.fullmatch(value) is not None and not value.startswith("rexit.")


def receipt_path(signal_id: str, side: str) -> Path:
    if not _journal_id(signal_id) or side not in {"buy", "sell"}:
        raise JournalError("unsafe journal path")
    return Path("journal") / signal_id / f"{side}.json"


def mark_path(buy_id: str, n: int) -> Path:
    if not _SIGNAL_ID.fullmatch(buy_id) or buy_id.startswith("rexit.") or n < 1:
        raise JournalError("unsafe mark path")
    return Path("journal") / buy_id / "marks" / f"{n}.json"


def commit_record(root: Path, relative: Path, payload: dict, *, crash: bool = False) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.tmp"
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    if crash:
        raise CrashBeforeRename(temporary)
    os.replace(temporary, target)
    directory = os.open(target.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def discard_temps(root: Path) -> None:
    if not root.exists():
        return
    for path in root.rglob("*.tmp"):
        path.unlink()


def _stored_quote(body: object) -> StoredQuote | None:
    if body is None:
        return None
    if not isinstance(body, dict):
        raise JournalError("bad quote")
    return StoredQuote(
        body.get("router"),
        body["inAmount"],
        body["outAmount"],
        body.get("feeBps"),
        body.get("feeMint"),
        body.get("platformFee"),
    )


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    parsed = parse_nonneg(value)
    if parsed is None:
        raise JournalError("bad integer")
    return parsed


def load_records(root: Path) -> tuple[list[Receipt], list[MarkRecord]]:
    receipts: list[Receipt] = []
    marks: list[MarkRecord] = []
    journal = root / "journal"
    if not journal.exists():
        return receipts, marks
    for path in sorted(journal.rglob("*.json")):
        if path.name.startswith("."):
            continue
        body = json.loads(path.read_text(encoding="utf-8"))
        if body.get("record_type") == "jupiter_dry_run_receipt":
            receipts.append(
                Receipt(
                    body["id"],
                    body["side"],
                    body["outcome"],
                    body["trigger"],
                    body["mint"],
                    body["quoted_in_amount"],
                    body["quoted_out_amount"],
                    body["reason"],
                    body["denylist"],
                    parse_pos(body["seq"]) or 0,
                    _optional_int(body["buy_lamports"]),
                    _optional_int(body["estimated_fee_lamports"]),
                    _optional_int(body["estimated_rent_lamports"]),
                    _stored_quote(body["quote"]),
                )
            )
        elif body.get("record_type") == "jupiter_dry_run_mark":
            marks.append(
                MarkRecord(
                    body["buy_id"],
                    parse_pos(body["n"]) or 0,
                    parse_pos(body["mark_out_lamports"]) or 0,
                    parse_pos(body["mark_in_base_units"]) or 0,
                    parse_pos(body["seq"]) or 0,
                    _stored_quote(body["quote"]),
                )
            )
        else:
            raise JournalError("unknown record")
    if any(item.seq < 1 for item in receipts) or any(item.seq < 1 or item.n < 1 for item in marks):
        raise JournalError("bad seq")
    return receipts, marks
```

`parse_pos` rejects `"0"`, so a missing parse becomes `0` and then `JournalError`. That is intentional.

- [x] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_journal -v`

Expected: PASS, `Ran 1 test`

- [x] **Step 5: Commit**

```bash
git add executor/journal.py tests/test_journal.py
git commit -m "feat: commit journal records with fsync and rename"
```

### Task 12: Simulated book fold

**Files:**
- Create: `executor/book.py`
- Create: `tests/test_book.py`

**Interfaces:**
- Consumes: `Book`, `MarkRecord`, `Receipt`, `StoredQuote`, `apply_observation`, `MINT_A`
- Produces: `fold(starting_bank: int, receipts: list[Receipt], marks: list[MarkRecord]) -> Book`

- [x] **Step 1: Write the failing test**

```python
# tests/test_book.py
import unittest

from executor.book import fold
from executor.types import MarkRecord, Receipt, StoredQuote
from tests.fixtures import MINT_A


def quote(in_amount, out_amount):
    return StoredQuote("metis", in_amount, out_amount, None, None, None)


def buy(seq, out_amount="1000"):
    return Receipt(
        "buy1", "buy", "quoted", "signal", MINT_A, "80000000", out_amount, None,
        "not_configured", seq, 80_000_000, 5000, 0, quote("80000000", out_amount),
    )


def sell(seq, signal_id="sell1"):
    return Receipt(
        signal_id, "sell", "quoted", "signal", MINT_A, "1000", "70000000", None,
        "not_configured", seq, None, None, None, quote("1000", "70000000"),
    )


class BookTest(unittest.TestCase):
    def test_fold_is_commutative_for_one_cycle_and_ordered_for_reopen(self):
        book = fold(1_000_000_000, [buy(1)], [])
        self.assertEqual(book.bank_lamports, 919_995_000)
        self.assertEqual(book.positions[MINT_A].token_base_units, 1000)
        self.assertFalse(book.positions[MINT_A].armed)
        closed = fold(1_000_000_000, [sell(2), buy(1)], [])
        self.assertEqual(closed.positions, {})
        self.assertEqual(closed.bank_lamports, 919_995_000 + 70_000_000)
        rejected = Receipt(
            "buy2", "buy", "rejected", "signal", MINT_A, None, None, "risk_cap",
            "not_configured", 3, None, None, None, None,
        )
        self.assertEqual(fold(1_000_000_000, [rejected], []).bank_lamports, 1_000_000_000)
        mark = MarkRecord("buy1", 1, 120_000_000, 1000, 2, quote("1000", "120000000"))
        armed = fold(1_000_000_000, [buy(1, "1000")], [mark])
        # entry is 80000000/1000; mark 120000000/1000 is above +12 percent
        self.assertTrue(armed.positions[MINT_A].armed)
        self.assertEqual(armed.positions[MINT_A].high_out_lamports, 120_000_000)


if __name__ == "__main__":
    unittest.main()
```

The arm assertion uses entry `80_000_000 / 1000` and mark `120_000_000 / 1000`. `120_000_000 * 1000 * 100 >= 80_000_000 * 1000 * 112` is `12_000_000_000_000 >= 8_960_000_000_000`, so it arms. The high becomes the mark because `120_000_000 * 1000 > 80_000_000 * 1000`.

- [x] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_book -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'executor.book'`

- [x] **Step 3: Write minimal implementation**

```python
# executor/book.py
from executor.marks import apply_observation
from executor.types import Book, MarkRecord, Position, Receipt


def fold(starting_bank: int, receipts: list[Receipt], marks: list[MarkRecord]) -> Book:
    bank = starting_bank
    positions: dict[str, Position] = {}
    for receipt in sorted(receipts, key=lambda item: item.seq):
        if receipt.outcome != "quoted":
            continue
        if receipt.side == "buy":
            if receipt.buy_lamports is None or receipt.estimated_fee_lamports is None or receipt.estimated_rent_lamports is None:
                raise ValueError("quoted buy is missing its cost")
            if receipt.quote is None:
                raise ValueError("quoted buy is missing its quote")
            out_units = int(receipt.quote.out_amount)
            bank -= receipt.buy_lamports + receipt.estimated_fee_lamports + receipt.estimated_rent_lamports
            positions[receipt.mint] = Position(
                receipt.id,
                receipt.mint,
                receipt.buy_lamports,
                out_units,
                out_units,
                receipt.buy_lamports,
                out_units,
                False,
            )
        else:
            if receipt.quote is None:
                raise ValueError("quoted sell is missing its quote")
            bank += int(receipt.quote.out_amount)
            positions.pop(receipt.mint, None)
    for mark in sorted(marks, key=lambda item: (item.buy_id, item.n)):
        for mint, position in list(positions.items()):
            if position.buy_id != mark.buy_id:
                continue
            positions[mint] = apply_observation(position, mark.mark_out_lamports, mark.mark_in_base_units)
    return Book(bank, positions)
```

- [x] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_book -v`

Expected: PASS, `Ran 1 test`

- [x] **Step 5: Commit**

```bash
git add executor/book.py tests/test_book.py
git commit -m "feat: fold the journal into a simulated book"
```

### Task 13: Process lock

**Files:**
- Create: `executor/lock.py`
- Create: `tests/test_lock.py`

**Interfaces:**
- Consumes: nothing
- Produces: `LockBusy`, `exclusive_lock(path: Path)` context manager. The second acquire raises `LockBusy` and does not replace the file.

- [x] **Step 1: Write the failing test**

```python
# tests/test_lock.py
import tempfile
import unittest
from pathlib import Path

from executor.lock import LockBusy, exclusive_lock


class LockTest(unittest.TestCase):
    def test_second_acquire_fails_and_a_released_lock_can_be_taken(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "executor.lock"
        with exclusive_lock(path):
            with self.assertRaises(LockBusy):
                with exclusive_lock(path):
                    pass
        with exclusive_lock(path):
            self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_lock -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'executor.lock'`

- [x] **Step 3: Write minimal implementation**

```python
# executor/lock.py
import fcntl
from contextlib import contextmanager
from pathlib import Path


class LockBusy(Exception):
    pass


@contextmanager
def exclusive_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise LockBusy()
    try:
        yield handle
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
```

- [x] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_lock -v`

Expected: PASS, `Ran 1 test`

- [x] **Step 5: Commit**

```bash
git add executor/lock.py tests/test_lock.py
git commit -m "feat: add a non-blocking executor lock"
```

### Task 14: Signal run loop, dedup, and isolated receipts

**Files:**
- Create: `executor/run.py`
- Create: `tests/test_run.py`

**Interfaces:**
- Consumes: every signature from Tasks 3 through 13, plus `denylist_label`, `log_schema`, `quote_once`, `prequote`, `fold`, `exclusive_lock`
- Produces: `StopFlag`, `run(config: DryRunConfig, inbox: Path, state: Path, transport, *, crash_commits: int = 0, stop: StopFlag | None = None) -> int`

`run` returns `0` after one pass. It raises `LockBusy` when the lock is held. It does not call `executor.signer.sign`. This task handles inbox signals only. Task 15 adds the mark pass at the end.

Behavior for this task:

- After the lock, discard `*.tmp`.
- Read only top-level `*.json` inbox files, read-only.
- Sort valid signals by `sort_key`. Schema failures with a safe id get a terminal rejected receipt and no quote. Invalid JSON or an unsafe id logs `schema` and writes no journal file.
- If `journal/<id>/<side>.json` already exists, do not quote.
- `prequote` rejection commits `outcome: rejected`, `trigger: signal`, null quote amounts, and no transport call.
- A buy quote uses `QuoteRequest(SOL_MINT, mint, str(size_lamports))`. A sell quote uses `QuoteRequest(mint, SOL_MINT, str(token_amount))`.
- `QuoteSuccess` commits `outcome: quoted` and the stored quote. `QuoteUnsafe` commits `reason: quote_unsafe`. `QuoteUnavailable` commits nothing.
- Refold from the journal after every commit. `seq` is one plus the highest seq already committed.
- A quoted buy stores `buy_lamports` and the estimate. A quoted sell stores those three integers as null.
- `stop.is_set()` before each signal returns `0` without a new sell file.
- `crash_commits` makes the next commit raise `CrashBeforeRename` after the temp file exists.

- [x] **Step 1: Write the failing test**

```python
# tests/test_run.py
import json
import tempfile
import unittest
from pathlib import Path

from executor.config import load_config
from executor.journal import CrashBeforeRename, load_records
from executor.run import StopFlag, run
from executor.signer import sign
from executor.types import SOL_MINT
from tests.fixtures import MINT_A, MINT_B, MINT_C, MINT_D, sample_config


class FakeTransport:
    def __init__(self, out_amounts):
        self.out_amounts = list(out_amounts)
        self.calls = []

    def get(self, url, headers):
        self.calls.append((url, dict(headers)))
        amount = url.split("amount=")[1]
        out_amount = self.out_amounts.pop(0)
        body = {
            "router": "metis",
            "inputMint": SOL_MINT if "inputMint=" + SOL_MINT in url else MINT_A,
            "outputMint": MINT_A if "inputMint=" + SOL_MINT in url else SOL_MINT,
            "inAmount": amount,
            "outAmount": out_amount,
            "transaction": None,
        }
        return 200, body


def write_config(directory: Path, estimates):
    body = sample_config(
        buy_estimates={
            key: {"estimated_fee_lamports": str(fee), "estimated_rent_lamports": "0"}
            for key, fee in estimates.items()
        }
    )
    path = directory / "config.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return load_config(path)


def write_signal(inbox: Path, name: str, body: dict):
    inbox.mkdir(parents=True, exist_ok=True)
    path = inbox / name
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.inbox = self.root / "inbox"
        self.state = self.root / "state"

    def test_buy_sell_dedup_and_no_signer(self):
        config = write_config(self.root, {"buy1": 5000, "buy2": 5000})
        signal = {
            "id": "buy1",
            "side": "buy",
            "mint": MINT_A,
            "created_at": "2026-11-07T10:00:00+02:00",
            "size_sol": "0.08",
        }
        path = write_signal(self.inbox, "a.json", signal)
        before = path.read_bytes()
        transport = FakeTransport(["1000", "70000000"])
        self.assertEqual(run(config, self.inbox, self.state, transport), 0)
        write_signal(
            self.inbox,
            "b.json",
            {
                "id": "sell1",
                "side": "sell",
                "mint": MINT_A,
                "created_at": "2026-11-07T10:01:00+02:00",
                "token_amount": "1000",
            },
        )
        self.assertEqual(run(config, self.inbox, self.state, transport), 0)
        self.assertEqual(run(config, self.inbox, self.state, transport), 0)
        self.assertEqual(len(transport.calls), 2)
        self.assertNotIn("taker", transport.calls[0][0])
        self.assertEqual(transport.calls[0][1], {})
        receipts, _marks = load_records(self.state)
        self.assertEqual(sorted(item.id for item in receipts), ["buy1", "sell1"])
        self.assertTrue(all(item.outcome == "quoted" for item in receipts))
        self.assertEqual(path.read_bytes(), before)
        called = {"value": False}

        def boom(payload):
            called["value"] = True
            raise AssertionError(payload)

        import executor.signer as signer_mod

        signer_mod.sign = boom
        self.assertEqual(run(config, self.inbox, self.state, transport), 0)
        self.assertFalse(called["value"])
        with self.assertRaises(Exception):
            sign(b"nope")

    def test_risk_rejection_does_not_quote_and_unsafe_is_terminal(self):
        config = write_config(self.root, {"buy1": 5000})
        write_signal(
            self.inbox,
            "a.json",
            {
                "id": "buy1",
                "side": "buy",
                "mint": MINT_A,
                "created_at": "2026-11-07T10:00:00+02:00",
                "size_sol": "0.09",
            },
        )
        transport = FakeTransport(["1000"])
        run(config, self.inbox, self.state, transport)
        self.assertEqual(transport.calls, [])
        receipts, _marks = load_records(self.state)
        self.assertEqual(receipts[0].reason, "risk_cap")
        self.assertEqual(receipts[0].denylist, "not_configured")

    def test_crash_before_rename_quotes_once_on_recovery(self):
        config = write_config(self.root, {"buy1": 5000})
        write_signal(
            self.inbox,
            "a.json",
            {
                "id": "buy1",
                "side": "buy",
                "mint": MINT_A,
                "created_at": "2026-11-07T10:00:00+02:00",
                "size_sol": "0.08",
            },
        )
        transport = FakeTransport(["1000", "1000"])
        with self.assertRaises(CrashBeforeRename):
            run(config, self.inbox, self.state, transport, crash_commits=1)
        self.assertEqual(len(transport.calls), 1)
        run(config, self.inbox, self.state, transport)
        self.assertEqual(len(transport.calls), 2)
        receipts, _marks = load_records(self.state)
        self.assertEqual(len(receipts), 1)
        run(config, self.inbox, self.state, transport)
        self.assertEqual(len(transport.calls), 2)

    def test_fourth_open_position_is_rejected(self):
        estimates = {f"buy{i}": 5000 for i in range(1, 6)}
        config = write_config(self.root, estimates)
        for index, mint in enumerate((MINT_A, MINT_B, MINT_C, MINT_D), start=1):
            write_signal(
                self.inbox,
                f"{index}.json",
                {
                    "id": f"buy{index}",
                    "side": "buy",
                    "mint": mint,
                    "created_at": f"2026-11-07T10:0{index}:00+02:00",
                    "size_sol": "0.01",
                },
            )
        transport = FakeTransport(["10", "10", "10"])
        run(config, self.inbox, self.state, transport)
        receipts, _marks = load_records(self.state)
        reasons = {item.id: item.reason for item in receipts}
        self.assertEqual(reasons["buy4"], "risk_max_positions")
        self.assertEqual(len(transport.calls), 3)

    def test_pair_unknown_mint_unsafe_and_transport_failure(self):
        config = write_config(self.root, {"buy1": 5000, "buy9": 5000})
        write_signal(
            self.inbox,
            "a.json",
            {
                "id": "buy1",
                "side": "buy",
                "mint": SOL_MINT,
                "created_at": "2026-11-07T10:00:00+02:00",
                "size_sol": "0.01",
            },
        )
        transport = FakeTransport([])
        run(config, self.inbox, self.state, transport)
        self.assertEqual(load_records(self.state)[0][0].reason, "pair")
        self.assertEqual(transport.calls, [])
        write_signal(
            self.inbox,
            "b.json",
            {
                "id": "buy9",
                "side": "buy",
                "mint": MINT_DENIED,
                "created_at": "2026-11-07T10:02:00+02:00",
                "size_sol": "0.01",
            },
        )
        run(config, self.inbox, self.state, transport)
        reasons = {item.id: item.reason for item in load_records(self.state)[0]}
        self.assertEqual(reasons["buy9"], "unknown_mint")
        self.assertEqual(transport.calls, [])

    def test_quote_unsafe_is_terminal_and_timeout_is_not(self):
        config = write_config(self.root, {"buy1": 5000, "buy2": 5000})
        write_signal(
            self.inbox,
            "a.json",
            {
                "id": "buy1",
                "side": "buy",
                "mint": MINT_A,
                "created_at": "2026-11-07T10:00:00+02:00",
                "size_sol": "0.01",
            },
        )

        class Unsafe:
            def __init__(self):
                self.calls = []

            def get(self, url, headers):
                self.calls.append(url)
                amount = url.split("amount=")[1]
                return 200, {"transaction": "AQID", "inAmount": amount, "outAmount": "10"}

        unsafe = Unsafe()
        run(config, self.inbox, self.state, unsafe)
        run(config, self.inbox, self.state, unsafe)
        self.assertEqual(len(unsafe.calls), 1)
        self.assertEqual(load_records(self.state)[0][0].reason, "quote_unsafe")
        journal_text = "\n".join(path.read_text(encoding="utf-8") for path in (self.state / "journal").rglob("*.json"))
        self.assertNotIn("AQID", journal_text)

        class Down:
            def __init__(self):
                self.calls = []

            def get(self, url, headers):
                self.calls.append(url)
                raise TimeoutError("slow")

        write_signal(
            self.inbox,
            "c.json",
            {
                "id": "buy2",
                "side": "buy",
                "mint": MINT_B,
                "created_at": "2026-11-07T10:03:00+02:00",
                "size_sol": "0.01",
            },
        )
        down = Down()
        run(config, self.inbox, self.state, down)
        self.assertEqual(len(down.calls), 1)
        self.assertFalse(any(item.id == "buy2" for item in load_records(self.state)[0]))

    def test_reopen_after_a_full_close(self):
        config = write_config(self.root, {"buy1": 5000, "buy2": 5000})
        write_signal(
            self.inbox,
            "a.json",
            {
                "id": "buy1",
                "side": "buy",
                "mint": MINT_A,
                "created_at": "2026-11-07T10:00:00+02:00",
                "size_sol": "0.01",
            },
        )
        write_signal(
            self.inbox,
            "b.json",
            {
                "id": "sell1",
                "side": "sell",
                "mint": MINT_A,
                "created_at": "2026-11-07T10:01:00+02:00",
                "token_amount": "10",
            },
        )
        transport = FakeTransport(["10", "5000000", "10"])
        run(config, self.inbox, self.state, transport)
        self.assertEqual(len(transport.calls), 2)
        write_signal(
            self.inbox,
            "c.json",
            {
                "id": "buy2",
                "side": "buy",
                "mint": MINT_A,
                "created_at": "2026-11-07T10:02:00+02:00",
                "size_sol": "0.01",
            },
        )
        run(config, self.inbox, self.state, transport)
        self.assertEqual(len(transport.calls), 3)
        quoted = {item.id for item in load_records(self.state)[0] if item.outcome == "quoted"}
        self.assertEqual(quoted, {"buy1", "sell1", "buy2"})


if __name__ == "__main__":
    unittest.main()
```

`0.01` SOL is `10_000_000` lamports. Three buys cost `30_015_000` lamports including fees, from a bank of `1_000_000_000`, so the first three pass the reserve. The fourth is rejected because three positions are already open. `MINT_DENIED` is absent from `sample_config` metadata, so it is `unknown_mint`. The bytes `AQID` must not appear in the journal.

- [x] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_run -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'executor.run'`

- [x] **Step 3: Write minimal implementation**

```python
# executor/run.py
import json
from pathlib import Path

from executor.book import fold
from executor.journal import CrashBeforeRename, commit_record, discard_temps, load_records, receipt_path
from executor.lock import exclusive_lock
from executor.quote import quote_once
from executor.records import receipt_payload
from executor.risk import denylist_label, prequote
from executor.schema import log_schema, parse_signal
from executor.types import SOL_MINT, DryRunConfig, QuoteRequest, QuoteSuccess, QuoteUnavailable, QuoteUnsafe, Receipt, SchemaRejection, Signal


class StopFlag:
    def __init__(self):
        self._set = False

    def set(self):
        self._set = True

    def is_set(self):
        return self._set


def _signals(inbox: Path) -> list[Signal | SchemaRejection]:
    parsed: list[Signal | SchemaRejection] = []
    if not inbox.exists():
        return parsed
    for path in sorted(p for p in inbox.iterdir() if p.is_file() and p.suffix == ".json"):
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            rejection = SchemaRejection(None, False)
            log_schema(rejection)
            continue
        item = parse_signal(body)
        if isinstance(item, SchemaRejection):
            log_schema(item)
        parsed.append(item)
    return parsed


def _refold(config: DryRunConfig, state: Path):
    receipts, marks = load_records(state)
    return receipts, marks, fold(config.bank_lamports, receipts, marks)


def _commit(state: Path, receipt: Receipt, crash_commits: list[int]):
    payload = receipt_payload(receipt)
    crash = crash_commits[0] > 0
    if crash:
        crash_commits[0] -= 1
    commit_record(state, receipt_path(receipt.id, receipt.side), payload, crash=crash)


def run(config: DryRunConfig, inbox: Path, state: Path, transport, *, crash_commits: int = 0, stop: StopFlag | None = None) -> int:
    flag = stop or StopFlag()
    crashes = [crash_commits]
    with exclusive_lock(state / "executor.lock"):
        discard_temps(state)
        pending = _signals(inbox)
        valid = sorted((item for item in pending if isinstance(item, Signal)), key=lambda item: item.sort_key)
        for item in pending:
            if not isinstance(item, SchemaRejection) or not item.log_id or item.id is None or item.side not in {"buy", "sell"}:
                continue
            receipts, marks, _book = _refold(config, state)
            if any(record.id == item.id and record.side == item.side for record in receipts):
                continue
            _commit(
                state,
                Receipt(item.id, item.side, "rejected", "signal", SOL_MINT, None, None, "schema", denylist_label(config), _next_seq(receipts, marks), None, None, None, None),
                crashes,
            )
        for signal in valid:
            if flag.is_set():
                return 0
            receipts, marks, book = _refold(config, state)
            if any(record.id == signal.id and record.side == signal.side for record in receipts):
                continue
            reason = prequote(config, book, signal)
            seq = _next_seq(receipts, marks)
            label = denylist_label(config)
            if reason is not None:
                _commit(
                    state,
                    Receipt(signal.id, signal.side, "rejected", "signal", signal.mint, None, None, reason, label, seq, None, None, None, None),
                    crashes,
                )
                continue
            if signal.side == "buy":
                request = QuoteRequest(SOL_MINT, signal.mint, str(signal.size_lamports))
            else:
                request = QuoteRequest(signal.mint, SOL_MINT, str(signal.token_amount))
            result = quote_once(transport, request)
            if isinstance(result, QuoteUnavailable):
                continue
            if isinstance(result, QuoteUnsafe):
                _commit(
                    state,
                    Receipt(signal.id, signal.side, "rejected", "signal", signal.mint, None, None, "quote_unsafe", label, seq, None, None, None, None),
                    crashes,
                )
                continue
            assert isinstance(result, QuoteSuccess)
            estimate = config.buy_estimates.get(signal.id)
            _commit(
                state,
                Receipt(
                    signal.id,
                    signal.side,
                    "quoted",
                    "signal",
                    signal.mint,
                    result.quote.in_amount,
                    result.quote.out_amount,
                    None,
                    label,
                    seq,
                    signal.size_lamports if signal.side == "buy" else None,
                    estimate.estimated_fee_lamports if signal.side == "buy" and estimate else None,
                    estimate.estimated_rent_lamports if signal.side == "buy" and estimate else None,
                    result.quote,
                ),
                crashes,
            )
        return 0


def _next_seq(receipts, marks) -> int:
    seen = [item.seq for item in receipts] + [item.seq for item in marks]
    return max(seen, default=0) + 1
```

A schema rejection is journaled only when both `id` and `side` are known. Its `mint` is `SOL_MINT` because the signal did not prove a tradable mint. That value is not a quote and not a decimal guess. Invalid JSON and unsafe ids are logged and leave no journal file.

- [x] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_run tests.test_schema tests.test_types -v`

Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add executor/run.py executor/types.py executor/schema.py tests/test_run.py
git commit -m "feat: run one dry-run pass over inbox copies"
```

### Task 15: Mark, stop, and trailing persistence

**Files:**
- Modify: `executor/run.py`
- Modify: `tests/test_run.py`

**Interfaces:**
- Consumes: `decide_mark`, `mark_path`, `mark_payload`, `MarkRecord`, `QuoteRequest`, `QuoteSuccess`, `QuoteUnavailable`, `QuoteUnsafe`, `run`
- Produces: the same `run`. After inbox signals, for each open position in mint order, it requests one sell-to-SOL quote of `token_base_units`. `stop` and `trailing` commit one sell receipt, id `rexit.<buy_id>.stop` or `rexit.<buy_id>.trailing`, `trigger` set, `reason` null, and no mark file. `observe` commits `journal/<buy_id>/marks/<n>.json` and no exit. `QuoteUnsafe` and `QuoteUnavailable` commit nothing for that mark.

- [x] **Step 1: Write the failing test**

Append to `tests/test_run.py`:

```python
    def test_stop_commits_one_exit_and_no_mark_file(self):
        config = write_config(self.root, {"buy1": 0})
        write_signal(
            self.inbox,
            "a.json",
            {
                "id": "buy1",
                "side": "buy",
                "mint": MINT_A,
                "created_at": "2026-11-07T10:00:00+02:00",
                "size_sol": "0.1",
            },
        )
        transport = FakeTransport(["1000000000", "94000000"])
        run(config, self.inbox, self.state, transport)
        receipts, marks = load_records(self.state)
        exits = [item for item in receipts if item.trigger == "stop"]
        self.assertEqual([item.id for item in exits], ["rexit.buy1.stop"])
        self.assertEqual(exits[0].reason, None)
        self.assertEqual(exits[0].outcome, "quoted")
        self.assertEqual(marks, [])
        calls = len(transport.calls)
        run(config, self.inbox, self.state, transport)
        self.assertEqual(len(transport.calls), calls)

    def test_arm_then_giveback_and_stop_wins_when_both_match(self):
        config = write_config(self.root, {"buy1": 0})
        write_signal(
            self.inbox,
            "a.json",
            {
                "id": "buy1",
                "side": "buy",
                "mint": MINT_A,
                "created_at": "2026-11-07T10:00:00+02:00",
                "size_sol": "0.1",
            },
        )
        transport = FakeTransport(["1000000000", "112000000", "120000000", "112800000"])
        run(config, self.inbox, self.state, transport)
        _receipts, marks = load_records(self.state)
        self.assertEqual([item.n for item in marks], [1])
        run(config, self.inbox, self.state, transport)
        _receipts, marks = load_records(self.state)
        self.assertEqual([item.n for item in marks], [1, 2])
        run(config, self.inbox, self.state, transport)
        receipts, marks = load_records(self.state)
        self.assertEqual([item.n for item in marks], [1, 2])
        self.assertEqual(
            [item.id for item in receipts if item.trigger == "trailing"],
            ["rexit.buy1.trailing"],
        )
        calls = len(transport.calls)
        run(config, self.inbox, self.state, transport)
        self.assertEqual(len(transport.calls), calls)
```

`size_sol` `0.1` is `100_000_000` lamports. The first quote output `1000000000` is the token base units. Mark outputs `94000000`, `112000000`, `120000000`, and `112800000` are the SOL lamports returned for selling those tokens. They match Task 9.

For the stop-wins case, use a new test method and a fresh directory from `setUp`. First two calls arm and set the peak, then the third output is `94000000`:

```python
    def test_stop_wins_after_the_peak(self):
        config = write_config(self.root, {"buy1": 0})
        write_signal(
            self.inbox,
            "a.json",
            {
                "id": "buy1",
                "side": "buy",
                "mint": MINT_A,
                "created_at": "2026-11-07T10:00:00+02:00",
                "size_sol": "0.1",
            },
        )
        transport = FakeTransport(["1000000000", "120000000", "94000000"])
        run(config, self.inbox, self.state, transport)
        run(config, self.inbox, self.state, transport)
        receipts, _marks = load_records(self.state)
        self.assertEqual([item.id for item in receipts if item.side == "sell"], ["rexit.buy1.stop"])

    def test_unsafe_mark_commits_nothing_and_can_retry(self):
        config = write_config(self.root, {"buy1": 0})
        write_signal(
            self.inbox,
            "a.json",
            {
                "id": "buy1",
                "side": "buy",
                "mint": MINT_A,
                "created_at": "2026-11-07T10:00:00+02:00",
                "size_sol": "0.1",
            },
        )

        class OnceUnsafe:
            def __init__(self):
                self.calls = 0

            def get(self, url, headers):
                self.calls += 1
                amount = url.split("amount=")[1]
                if self.calls == 1:
                    return 200, {"transaction": None, "inAmount": amount, "outAmount": "1000000000", "router": "metis"}
                return 200, {"transaction": "AQID", "inAmount": amount, "outAmount": "1000000000"}

        transport = OnceUnsafe()
        run(config, self.inbox, self.state, transport)
        receipts, marks = load_records(self.state)
        self.assertEqual([item.outcome for item in receipts], ["quoted"])
        self.assertEqual(marks, [])
        self.assertNotIn("AQID", "\n".join(path.read_text(encoding="utf-8") for path in (self.state / "journal").rglob("*.json")))
        run(config, self.inbox, self.state, transport)
        self.assertGreater(transport.calls, 2)
```

The first `run` buys and marks `120000000`, which arms. The second `run` marks `94000000` and must choose stop, not trailing. An unsafe mark stores no bytes and does not block the next invocation's mark quote.

- [x] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_run.RunTest.test_stop_commits_one_exit_and_no_mark_file -v`

Expected: FAIL because no stop receipt exists.

- [x] **Step 3: Write minimal implementation**

Add these imports to `executor/run.py`: `decide_mark` from `executor.marks`, `mark_path` from `executor.journal`, `mark_payload` from `executor.records`, and `MarkRecord` from `executor.types`.

Insert this function before `run`:

```python
def _apply_marks(config, state, transport, flag, crashes):
    _receipts, _marks, book = _refold(config, state)
    for mint in sorted(book.positions):
        if flag.is_set():
            return
        position = book.positions[mint]
        receipts, marks, book = _refold(config, state)
        position = book.positions[mint]
        result = quote_once(
            transport,
            QuoteRequest(position.mint, SOL_MINT, str(position.token_base_units)),
        )
        if isinstance(result, (QuoteUnavailable, QuoteUnsafe)):
            continue
        assert isinstance(result, QuoteSuccess)
        decision = decide_mark(position, int(result.quote.out_amount), int(result.quote.in_amount))
        seq = _next_seq(receipts, marks)
        if decision == "observe":
            number = max((item.n for item in marks if item.buy_id == position.buy_id), default=0) + 1
            commit_record(
                state,
                mark_path(position.buy_id, number),
                mark_payload(
                    MarkRecord(
                        position.buy_id,
                        number,
                        int(result.quote.out_amount),
                        int(result.quote.in_amount),
                        seq,
                        result.quote,
                    )
                ),
                crash=False,
            )
            continue
        exit_id = f"rexit.{position.buy_id}.{decision}"
        _commit(
            state,
            Receipt(
                exit_id,
                "sell",
                "quoted",
                decision,
                position.mint,
                result.quote.in_amount,
                result.quote.out_amount,
                None,
                denylist_label(config),
                seq,
                None,
                None,
                None,
                result.quote,
            ),
            crashes,
        )
```

Mark commits pass `crash=False`. Exit commits use `_commit`, which owns the signal crash counter. Replace the final `return 0` inside the lock with:

```python
        _apply_marks(config, state, transport, flag, crashes)
        return 0
```

- [x] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_run -v`

Expected: PASS, including the stop, arm, giveback, stop-wins, and unsafe-mark tests, plus the earlier signal tests.

- [x] **Step 5: Commit**

```bash
git add executor/run.py tests/test_run.py
git commit -m "feat: persist stop, trailing, and mark observations"
```

### Task 16: CLI acceptance, lock conflict, and SIGTERM

**Files:**
- Create: `executor/cli.py`
- Create: `executor/__main__.py`
- Create: `tests/test_cli.py`

**Interfaces:**
- Consumes: `ConfigError`, `load_config`, `LockBusy`, `find_startup_refusal`, `StopFlag`, `run`, `UrllibTransport`
- Produces: `main(argv: list[str] | None = None, environ: Mapping[str, str] | None = None) -> int`, `install_sigterm(stop: StopFlag) -> None`

`main` checks refusal before it reads the config, creates the state directory, or constructs `UrllibTransport`. Config errors return 3 and print `refused: config`. `LockBusy` returns 2. Missing `--config` or `--inbox` returns 1 and prints `usage`. `SIGTERM` sets the stop flag. The real transport is built only after refusal and config load, immediately before `run`.

- [x] **Step 1: Write the failing test**

```python
# tests/test_cli.py
import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path

from executor.cli import install_sigterm, main
from executor.run import StopFlag
from tests.fixtures import MINT_A, sample_config


class CliTest(unittest.TestCase):
    def test_live_refusal_creates_no_state_and_hides_the_secret(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state = Path(tmp.name) / "state"
        secret = "super-secret-value"
        code = main(["--live", "--state", str(state)], {"JUPITER_API_KEY": secret})
        self.assertEqual(code, 3)
        self.assertFalse(state.exists())

    def test_subprocess_mode_live_is_exit_3(self):
        result = subprocess.run(
            [sys.executable, "-m", "executor", "--config", "missing.json", "--inbox", "missing"],
            env={**os.environ, "MODE": "live"},
            capture_output=True,
            text=True,
            timeout=5,
            cwd=Path(__file__).resolve().parents[1],
        )
        self.assertEqual(result.returncode, 3)
        self.assertNotIn("missing.json", result.stderr)

    def test_second_process_exits_2_without_writing_journal(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        config = root / "config.json"
        config.write_text(json.dumps(sample_config()), encoding="utf-8")
        inbox = root / "inbox"
        inbox.mkdir()
        state = root / "state"
        ready = root / "ready"
        held = subprocess.Popen(
            [
                sys.executable,
                "-c",
                textwrap.dedent(
                    """
                    import sys
                    import time
                    from pathlib import Path
                    from executor.lock import exclusive_lock
                    with exclusive_lock(Path(sys.argv[1])):
                        Path(sys.argv[2]).write_text("held", encoding="utf-8")
                        time.sleep(5)
                    """
                ),
                str(state / "executor.lock"),
                str(ready),
            ],
            cwd=Path(__file__).resolve().parents[1],
        )
        result = None
        try:
            for _ in range(50):
                if ready.exists():
                    break
                time.sleep(0.05)
            self.assertTrue(ready.exists())
            result = subprocess.run(
                [sys.executable, "-m", "executor", "--config", str(config), "--inbox", str(inbox), "--state", str(state)],
                capture_output=True,
                text=True,
                timeout=5,
                cwd=Path(__file__).resolve().parents[1],
            )
        finally:
            held.send_signal(signal.SIGTERM)
            held.wait(timeout=5)
        self.assertIsNotNone(result)
        self.assertEqual(result.returncode, 2)
        self.assertFalse((state / "journal").exists())

    def test_sigterm_during_quote_writes_no_sell(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        config_path = root / "config.json"
        body = sample_config(buy_estimates={"buy1": {"estimated_fee_lamports": "0", "estimated_rent_lamports": "0"}})
        config_path.write_text(json.dumps(body), encoding="utf-8")
        inbox = root / "inbox"
        inbox.mkdir()
        (inbox / "a.json").write_text(
            json.dumps(
                {
                    "id": "buy1",
                    "side": "buy",
                    "mint": MINT_A,
                    "created_at": "2026-11-07T10:00:00+02:00",
                    "size_sol": "0.01",
                }
            ),
            encoding="utf-8",
        )
        stop = StopFlag()
        previous = signal.getsignal(signal.SIGTERM)
        self.addCleanup(lambda: signal.signal(signal.SIGTERM, previous))
        install_sigterm(stop)
        entered = threading.Event()
        release = threading.Event()

        class Hold:
            def get(self, url, headers):
                entered.set()
                release.wait(5)
                amount = url.split("amount=")[1]
                return 200, {"transaction": None, "inAmount": amount, "outAmount": "10", "router": "metis"}

        def poke():
            self.assertTrue(entered.wait(5))
            os.kill(os.getpid(), signal.SIGTERM)
            release.set()

        threading.Thread(target=poke).start()
        from executor.config import load_config
        from executor.run import run

        code = run(load_config(config_path), inbox, root / "state", Hold(), stop=stop)
        self.assertEqual(code, 0)
        journal = root / "state" / "journal"
        text = "" if not journal.exists() else "\n".join(path.read_text() for path in journal.rglob("*.json"))
        self.assertNotIn('"side":"sell"', text.replace(" ", ""))
        self.assertNotIn("liquidation", text)

    def test_wallet_is_not_hardcoded(self):
        text = "\n".join(path.read_text(encoding="utf-8") for path in Path("executor").rglob("*.py"))
        self.assertNotIn("8Xm6iFvk9J8rBi7BvRAbiZU2RAEE1b7AM714QmiEXQ8a", text)


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_cli -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'executor.cli'`

- [x] **Step 3: Write minimal implementation**

```python
# executor/cli.py
import signal
import sys
from collections.abc import Mapping
from pathlib import Path

from executor.config import ConfigError, load_config
from executor.lock import LockBusy
from executor.quote import UrllibTransport
from executor.run import StopFlag, run
from executor.startup import find_startup_refusal


def install_sigterm(stop: StopFlag) -> None:
    def handler(signum, frame):
        stop.set()

    signal.signal(signal.SIGTERM, handler)


def main(argv: list[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    env = dict(sys.environ if environ is None else environ)
    refusal = find_startup_refusal(args, env)
    if refusal is not None:
        print(refusal, file=sys.stderr)
        return 3
    values = {}
    for index in range(0, len(args), 2):
        values[args[index]] = args[index + 1]
    if "--config" not in values or "--inbox" not in values:
        print("usage", file=sys.stderr)
        return 1
    try:
        config = load_config(Path(values["--config"]))
    except (OSError, ConfigError, ValueError):
        print("refused: config", file=sys.stderr)
        return 3
    state = Path(values.get("--state", "data/jupiter-dry-runs"))
    stop = StopFlag()
    install_sigterm(stop)
    try:
        return run(config, Path(values["--inbox"]), state, UrllibTransport(), stop=stop)
    except LockBusy:
        print("lock busy", file=sys.stderr)
        return 2
```

```python
# executor/__main__.py
import sys

from executor.cli import main

if __name__ == "__main__":
    sys.exit(main())
```

The subprocess `MODE=live` test passes `--config missing.json`. Refusal happens before the file is opened, so stderr is `refused: live` and does not contain `missing.json`. `find_startup_refusal` sees `MODE=live` before argv parsing. Good.

The lock subprocess imports `executor.lock` while the parent project is the cwd. Good.

- [x] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_cli tests.test_run tests.test_startup -v`

Expected: PASS. The live subprocess exits 3. The locked subprocess exits 2. SIGTERM leaves no sell receipt.

- [x] **Step 5: Commit**

```bash
git add executor/cli.py executor/__main__.py tests/test_cli.py
git commit -m "feat: add the dry-run CLI and signal handling"
```

### Task 17: Optional public quote smoke outside the default suite

**Files:**
- Create: `executor/smoke.py`
- Create: `tests/test_suite_scope.py`

**Interfaces:**
- Consumes: `QuoteRequest`, `SOL_MINT`, `USDC_MINT`, `SMOKE_AMOUNT`, `order_url`, `classify`, `UrllibTransport`
- Produces: `smoke_request() -> QuoteRequest`, `executor.smoke.main() -> int`

`main` performs one GET, prints `router`, `inAmount`, `outAmount`, and `feeBps` separated by spaces, writes no file, and returns 1 on `QuoteUnavailable`, `QuoteUnsafe`, or a transport exception. The default suite never calls it.

- [x] **Step 1: Write the failing test**

```python
# tests/test_suite_scope.py
import unittest
from pathlib import Path

from executor.quote import order_url
from executor.smoke import smoke_request


class SuiteScopeTest(unittest.TestCase):
    def test_default_suite_does_not_call_smoke_or_open_a_socket(self):
        text = "\n".join(path.read_text(encoding="utf-8") for path in Path("tests").rglob("test_*.py"))
        self.assertNotIn("executor.smoke.main", text)
        self.assertNotIn("urlopen", text)
        url = order_url(smoke_request())
        self.assertIn("amount=100000000", url)
        self.assertNotIn("taker", url)


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_suite_scope -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'executor.smoke'`

- [x] **Step 3: Write minimal implementation**

```python
# executor/smoke.py
import sys

from executor.quote import classify, order_url, UrllibTransport
from executor.types import SMOKE_AMOUNT, SOL_MINT, USDC_MINT, QuoteRequest, QuoteSuccess


def smoke_request() -> QuoteRequest:
    return QuoteRequest(SOL_MINT, USDC_MINT, SMOKE_AMOUNT)


def main() -> int:
    request = smoke_request()
    try:
        status, body = UrllibTransport().get(order_url(request), {})
    except (TimeoutError, OSError):
        print("smoke_unavailable", file=sys.stderr)
        return 1
    result = classify(request, status, body)
    if not isinstance(result, QuoteSuccess):
        print("smoke_unavailable", file=sys.stderr)
        return 1
    print(result.quote.router, result.quote.in_amount, result.quote.out_amount, result.quote.fee_bps)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [x] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s tests -v`

Expected: PASS for the whole default suite. The output does not run `executor.smoke.main`. No test opens a socket.

- [x] **Step 5: Commit**

```bash
git add executor/smoke.py tests/test_suite_scope.py
git commit -m "feat: add an optional quote smoke outside the default suite"
```

## Spec coverage

- Startup refusal, mandate expiry, and no dormant live path: Tasks 5 and 16.
- Exclusive lock and exit 2: Tasks 13 and 16.
- Inbox copy, schema, ordering, and read-only files: Tasks 4 and 14.
- Journal key `(id, side)`, fsync/rename, temp discard, and one receipt file: Tasks 7, 11, and 14.
- Cap, reserve, three positions, one mint, sell size, denylist, unknown decimals presence, and pair: Tasks 3, 8, and 14.
- Stop, arm, giveback, one exit, and durable marks: Tasks 9, 12, and 15.
- Quote URL, null transaction, unsafe versus unavailable, no API key, no fee table: Task 10.
- Signer cannot succeed and the quote path does not call it: Tasks 6 and 14.
- SIGTERM writes no sell and no liquidation record: Task 16.
- Smoke is outside the default suite: Task 17.
- Scanner migration, withdrawals, signing, `/execute`, and future live recovery: no task. Do not add them.

## Not in this plan

Do not implement live trading, a mandate renewal, Phantom `solana_send`, a local hot key, wallet creation, withdrawals, staking, lending, scanner migration, or deployment. The "Future live rules" section of the spec stays documentation.
