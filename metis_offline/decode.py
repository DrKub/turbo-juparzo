import base64
import binascii

from solders.message import MessageV0
from solders.transaction import VersionedTransaction

from metis_offline.report import DecodeReport, Intent


def check_slippage(intent: Intent) -> str:
    del intent
    return "slippage_unapproved"


def check_amount(intent: Intent, actual: int) -> str:
    if actual != intent.amount:
        return "intent_mismatch"
    return check_slippage(intent)


def _reject(reason: str, parsed: bool = False, version: str | None = None, fee_payer: bytes | None = None, zeros: bool | None = None) -> DecodeReport:
    return DecodeReport(parsed, version, reason, fee_payer, zeros)


def _compact_u16(raw: bytes, offset: int) -> tuple[int, int] | None:
    value = 0
    for shift in range(0, 21, 7):
        if offset >= len(raw):
            return None
        byte = raw[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte & 0x80 == 0:
            return value, offset
    return None


def _unsupported_version(raw: bytes) -> str | None:
    parsed = _compact_u16(raw, 0)
    if parsed is None:
        return None
    count, offset = parsed
    if count > 64:
        return None
    offset += count * 64
    if offset >= len(raw):
        return None
    marker = raw[offset]
    if marker & 0x80 == 0:
        return None
    number = marker & 0x7F
    if number == 0:
        return None
    return f"v{number}"


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
        version = _unsupported_version(raw)
        if version is not None:
            return _reject("unsupported_version", False, version, None, None)
        return _reject("truncated")
    version = "v0" if isinstance(tx.message, MessageV0) else "legacy"
    signatures = [bytes(sig) for sig in tx.signatures]
    header = tx.message.header
    if len(signatures) != header.num_required_signatures:
        return _reject("signature_count", True, version, None, None)
    zeros = all(sig == bytes(64) for sig in signatures)
    if not zeros:
        return _reject("signature_not_zero", True, version, None, False)
    if not tx.message.account_keys:
        return _reject("truncated", True, version, None, zeros)
    fee_payer = bytes(tx.message.account_keys[0])
    if header.num_readonly_signed_accounts > 0:
        return _reject("readonly", True, version, fee_payer, True)
    if fee_payer != intent.user:
        return _reject("fee_payer", True, version, fee_payer, True)
    if isinstance(tx.message, MessageV0):
        for lookup in tx.message.address_table_lookups:
            table_key = bytes(lookup.account_key)
            captured = intent.captured_tables.get(table_key)
            if captured is None or len(captured) % 32 != 0:
                return _reject("alt_missing", True, version, fee_payer, True)
            limit = len(captured) // 32
            indexes = list(lookup.writable_indexes) + list(lookup.readonly_indexes)
            if any(index >= limit for index in indexes):
                return _reject("alt_index", True, version, fee_payer, True)
    if len(tx.message.instructions) > 0:
        return _reject("unknown_program", True, version, fee_payer, True)
    return _reject(check_slippage(intent), True, version, fee_payer, True)
