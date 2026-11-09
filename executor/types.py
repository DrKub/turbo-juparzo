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
