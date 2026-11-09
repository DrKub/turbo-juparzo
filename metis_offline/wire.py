"""Parse and serialize Solana legacy and v0 transactions with the standard library.

Layout: compact-u16 signature count, 64-byte signatures, then the message. A v0
message starts with 0x80; any other high-bit prefix is an unsupported version.
Message: header (3 bytes), compact-u16 static keys (32 bytes each), 32-byte
recent blockhash, compact-u16 instructions (program index u8, compact-u16
account indexes, compact-u16 data), and for v0 compact-u16 lookups (32-byte
table, compact-u16 writable indexes, compact-u16 readonly indexes).
"""

from dataclasses import dataclass, field


class WireError(ValueError):
    def __init__(self, reason: str, version: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.version = version


@dataclass(frozen=True)
class CompiledInstruction:
    program_index: int
    accounts: tuple[int, ...]
    data: bytes


@dataclass(frozen=True)
class Lookup:
    table: bytes
    writable: tuple[int, ...]
    readonly: tuple[int, ...]


@dataclass(frozen=True)
class Transaction:
    signatures: tuple[bytes, ...]
    version: str
    num_required_signatures: int
    num_readonly_signed: int
    num_readonly_unsigned: int
    account_keys: tuple[bytes, ...]
    recent_blockhash: bytes
    instructions: tuple[CompiledInstruction, ...]
    lookups: tuple[Lookup, ...] = field(default_factory=tuple)
    message: bytes = b""


class _Reader:
    def __init__(self, raw: bytes, offset: int = 0):
        self.raw = raw
        self.offset = offset

    def take(self, size: int) -> bytes:
        if size < 0 or self.offset + size > len(self.raw):
            raise WireError("truncated")
        out = self.raw[self.offset : self.offset + size]
        self.offset += size
        return out

    def u8(self) -> int:
        return self.take(1)[0]

    def compact(self) -> int:
        value = 0
        for shift in (0, 7, 14):
            byte = self.u8()
            if shift == 14 and byte > 0x03:
                raise WireError("truncated")
            value |= (byte & 0x7F) << shift
            if byte & 0x80 == 0:
                return value
        raise WireError("truncated")


def parse_transaction(raw: bytes) -> Transaction:
    reader = _Reader(raw)
    count = reader.compact()
    if count > 64:
        raise WireError("truncated")
    signatures = tuple(reader.take(64) for _ in range(count))
    start = reader.offset
    if start >= len(raw):
        raise WireError("truncated")
    prefix = raw[start]
    if prefix & 0x80:
        number = prefix & 0x7F
        if number != 0:
            raise WireError("unsupported_version", f"v{number}")
        version = "v0"
        reader.offset += 1
    else:
        version = "legacy"
    header = reader.take(3)
    keys = tuple(reader.take(32) for _ in range(reader.compact()))
    blockhash = reader.take(32)
    instructions = []
    for _ in range(reader.compact()):
        program = reader.u8()
        accounts = tuple(reader.take(reader.compact()))
        data = reader.take(reader.compact())
        instructions.append(CompiledInstruction(program, accounts, data))
    lookups = []
    if version == "v0":
        for _ in range(reader.compact()):
            table = reader.take(32)
            writable = tuple(reader.take(reader.compact()))
            readonly = tuple(reader.take(reader.compact()))
            lookups.append(Lookup(table, writable, readonly))
    if reader.offset != len(raw):
        raise WireError("trailing_bytes", version)
    return Transaction(
        signatures,
        version,
        header[0],
        header[1],
        header[2],
        keys,
        blockhash,
        tuple(instructions),
        tuple(lookups),
        raw[start:],
    )


def compact(value: int) -> bytes:
    if not 0 <= value <= 0xFFFF:
        raise ValueError("compact-u16 out of range")
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def serialize(tx: Transaction) -> bytes:
    """Serialize with the given signatures. Used to build synthetic test fixtures."""
    body = bytearray()
    if tx.version == "v0":
        body.append(0x80)
    body += bytes([tx.num_required_signatures, tx.num_readonly_signed, tx.num_readonly_unsigned])
    body += compact(len(tx.account_keys)) + b"".join(tx.account_keys)
    body += tx.recent_blockhash
    body += compact(len(tx.instructions))
    for ix in tx.instructions:
        body.append(ix.program_index)
        body += compact(len(ix.accounts)) + bytes(ix.accounts)
        body += compact(len(ix.data)) + ix.data
    if tx.version == "v0":
        body += compact(len(tx.lookups))
        for lookup in tx.lookups:
            body += lookup.table
            body += compact(len(lookup.writable)) + bytes(lookup.writable)
            body += compact(len(lookup.readonly)) + bytes(lookup.readonly)
    return compact(len(tx.signatures)) + b"".join(tx.signatures) + bytes(body)
