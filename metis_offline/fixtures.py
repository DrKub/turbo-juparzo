"""Synthetic, stdlib-only fixtures for the offline preflight.

The user key, pool, table and blockhash are repeated byte patterns, not
wallets or accounts on any network. Program ids and mints are the real public
ids so the allowlist is exercised. Signatures are zero, so nothing here is
broadcastable.
"""

import base64
from dataclasses import dataclass, field, replace

from metis_offline.alt import ALT_PROGRAM, MAX_U64
from metis_offline.base58 import b58encode, pubkey
from metis_offline.pda import associated_token_address
from metis_offline.preflight import (
    ATA,
    COMPUTE_BUDGET,
    JUPITER,
    JUPITER_EVENT_AUTHORITY,
    ROUTE_V2,
    SYSTEM,
    TOKEN,
    USDC,
    WSOL,
    Evidence,
    Freshness,
    Intent,
    TableEvidence,
)
from metis_offline.wire import CompiledInstruction, Lookup, Transaction, serialize

USER = bytes([1]) * 32
POOL = bytes([2]) * 32
AMM_PROGRAM = pubkey("whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc")  # real public program id
TABLE = bytes([4]) * 32
BLOCKHASH = bytes([9]) * 32
WSOL_ATA = associated_token_address(USER, WSOL, TOKEN, ATA)
USDC_ATA = associated_token_address(USER, USDC, TOKEN, ATA)
AMOUNT = 1_000_000
QUOTED_OUT = 86_000
DIRECT_STEP = bytes.fromhex("2f010010270001")  # Whirlpool-style step: tag, payload, bps 10000, in 0, out 1

# Static keys: 0 user (signer, writable), 1 wsol ata (w), 2 usdc ata (w), then readonly.
STATIC = [USER, WSOL_ATA, USDC_ATA, SYSTEM, COMPUTE_BUDGET, TOKEN, ATA, JUPITER, JUPITER_EVENT_AUTHORITY]
I_USER, I_WSOL_ATA, I_USDC_ATA, I_SYSTEM, I_CB, I_TOKEN, I_ATA, I_JUP, I_EVENT = range(9)
# Loaded from the table: writable [POOL] -> 9, readonly [WSOL, USDC, AMM] -> 10, 11, 12.
TABLE_ADDRESSES = [POOL, WSOL, USDC, AMM_PROGRAM]
I_POOL, I_WSOL, I_USDC, I_AMM = 9, 10, 11, 12


def u32(value: int) -> bytes:
    return value.to_bytes(4, "little")


def u64(value: int) -> bytes:
    return value.to_bytes(8, "little")


def cu_limit_ix(limit: int) -> CompiledInstruction:
    return CompiledInstruction(I_CB, (), b"\x02" + u32(limit))


def cu_price_ix(price: int) -> CompiledInstruction:
    return CompiledInstruction(I_CB, (), b"\x03" + u64(price))


def ata_create_ix(ata_index: int = I_WSOL_ATA, mint_index: int = I_WSOL) -> CompiledInstruction:
    return CompiledInstruction(I_ATA, (I_USER, ata_index, I_USER, mint_index, I_SYSTEM, I_TOKEN), b"\x01")


def transfer_ix(lamports: int = AMOUNT, dest: int = I_WSOL_ATA) -> CompiledInstruction:
    return CompiledInstruction(I_SYSTEM, (I_USER, dest), u32(2) + u64(lamports))


def sync_ix() -> CompiledInstruction:
    return CompiledInstruction(I_TOKEN, (I_WSOL_ATA,), bytes([17]))


def close_ix(dest: int = I_USER) -> CompiledInstruction:
    return CompiledInstruction(I_TOKEN, (I_WSOL_ATA, dest, I_USER), bytes([9]))


def route_data(in_amount=AMOUNT, quoted_out=QUOTED_OUT, slippage=50, platform_fee=0, positive=0, steps=None) -> bytes:
    steps = [DIRECT_STEP] if steps is None else steps
    head = ROUTE_V2 + u64(in_amount) + u64(quoted_out)
    head += slippage.to_bytes(2, "little") + platform_fee.to_bytes(2, "little") + positive.to_bytes(2, "little")
    return head + u32(len(steps)) + b"".join(steps)


def route_ix(data: bytes | None = None, accounts: tuple[int, ...] | None = None) -> CompiledInstruction:
    if accounts is None:
        accounts = (I_USER, I_WSOL_ATA, I_USDC_ATA, I_WSOL, I_USDC, I_TOKEN, I_TOKEN, I_JUP, I_EVENT, I_JUP, I_AMM, I_POOL)
    return CompiledInstruction(I_JUP, accounts, route_data() if data is None else data)


def default_instructions(cu_limit: int | None = 90_000, cu_price: int = 1_000) -> list[CompiledInstruction]:
    out = []
    if cu_limit is not None:
        out.append(cu_limit_ix(cu_limit))
    out += [cu_price_ix(cu_price), ata_create_ix(), transfer_ix(), sync_ix(), route_ix(), close_ix()]
    return out


def table_data(addresses=None, deactivation=MAX_U64, last_extended=100, start=0, kind=1) -> bytes:
    addresses = TABLE_ADDRESSES if addresses is None else addresses
    header = u32(kind) + u64(deactivation) + u64(last_extended) + bytes([start])
    header += b"\0" * (56 - len(header))
    return header + b"".join(addresses)


@dataclass
class Case:
    instructions: list = field(default_factory=default_instructions)
    static: list = field(default_factory=lambda: list(STATIC))
    header: tuple = (1, 0, 6)
    lookups: list = field(default_factory=lambda: [Lookup(TABLE, (0,), (1, 2, 3))])
    signatures: list = field(default_factory=lambda: [bytes(64)])
    blockhash: bytes = BLOCKHASH
    version: str = "v0"
    intent: Intent = field(default_factory=lambda: Intent(USER, WSOL, USDC, AMOUNT, 50))
    tables: list = field(default_factory=lambda: [TableEvidence(TABLE, ALT_PROGRAM, table_data(), 200)])
    freshness: Freshness | None = field(default_factory=lambda: Freshness(BLOCKHASH, 1_000, 900))

    def transaction(self) -> Transaction:
        return Transaction(
            tuple(self.signatures),
            self.version,
            self.header[0],
            self.header[1],
            self.header[2],
            tuple(self.static),
            self.blockhash,
            tuple(self.instructions),
            tuple(self.lookups) if self.version == "v0" else (),
        )

    def raw(self) -> bytes:
        return serialize(self.transaction())

    def evidence(self) -> Evidence:
        return Evidence(tuple(self.tables), self.freshness)

    def request(self) -> dict:
        """A CLI request document for this synthetic case."""
        out = {
            "transaction_b64": base64.b64encode(self.raw()).decode("ascii"),
            "intent": {
                "user": b58encode(self.intent.user),
                "input_mint": b58encode(self.intent.input_mint),
                "output_mint": b58encode(self.intent.output_mint),
                "amount": self.intent.amount,
                "slippage_bps": self.intent.slippage_bps,
                "max_priority_fee_lamports": self.intent.max_priority_fee_lamports,
            },
            "lookup_tables": [
                {
                    "address": b58encode(t.address),
                    "owner": b58encode(t.owner),
                    "data_b64": base64.b64encode(t.data).decode("ascii"),
                    "context_slot": t.context_slot,
                }
                for t in self.tables
            ],
        }
        if self.freshness is not None:
            out["freshness"] = {
                "blockhash": b58encode(self.freshness.blockhash),
                "last_valid_block_height": self.freshness.last_valid_block_height,
                "current_block_height": self.freshness.current_block_height,
            }
        return out


__all__ = ["Case", "replace"]
