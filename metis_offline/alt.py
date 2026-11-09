"""Offline checks for a supplied address-lookup-table account.

The account data is a 56-byte header followed by 32-byte addresses:
- bytes 0..4: little-endian u32 state type, 1 for an initialized table
- bytes 4..12: deactivation slot, u64 max while the table is active
- bytes 12..20: last extended slot
- byte 20: start index of the last extension
- bytes 21..56: optional authority and padding
Addresses appended in the slot that is still current are not yet usable; the
usable count then stops at the last extension start index.
"""

from dataclasses import dataclass

from metis_offline.base58 import pubkey

ALT_PROGRAM = pubkey("AddressLookupTab1e1111111111111111111111111")
HEADER = 56
MAX_U64 = 2**64 - 1


@dataclass(frozen=True)
class Table:
    addresses: tuple[bytes, ...]
    deactivation_slot: int
    last_extended_slot: int
    start_index: int


def parse_table(data: bytes, owner: bytes) -> tuple[str | None, Table | None]:
    if owner != ALT_PROGRAM:
        return "alt_owner", None
    if len(data) < HEADER or (len(data) - HEADER) % 32 != 0:
        return "alt_layout", None
    if int.from_bytes(data[0:4], "little") != 1:
        return "alt_layout", None
    deactivation = int.from_bytes(data[4:12], "little")
    last_extended = int.from_bytes(data[12:20], "little")
    start = data[20]
    addresses = tuple(data[i : i + 32] for i in range(HEADER, len(data), 32))
    table = Table(addresses, deactivation, last_extended, start)
    if deactivation != MAX_U64:
        return "alt_deactivated", table
    return None, table


def usable_count(table: Table, context_slot: int | None) -> tuple[str | None, int]:
    if context_slot is None:
        return "alt_context", 0
    if table.last_extended_slot > context_slot:
        return "alt_context", 0
    if table.last_extended_slot == context_slot:
        return None, min(table.start_index, len(table.addresses))
    return None, len(table.addresses)
