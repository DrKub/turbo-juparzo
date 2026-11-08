from dataclasses import dataclass


@dataclass(frozen=True)
class Intent:
    user: bytes | None
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
