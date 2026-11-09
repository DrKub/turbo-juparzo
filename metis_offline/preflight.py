"""Offline semantic preflight for one unsigned Jupiter route_v2 SOL to USDC swap.

The check resolves v0 lookup tables from supplied on-chain account data and
walks every outer instruction against a closed allowlist. Anything it does not
understand fails closed. A pass is a semantic pass on an unsigned build; it is
never a trade authorization and never a signature check.
"""

import hashlib
from dataclasses import dataclass, field

from metis_offline.alt import parse_table, usable_count
from metis_offline.base58 import b58encode, pubkey
from metis_offline.pda import associated_token_address
from metis_offline.wire import Transaction, WireError, parse_transaction

# Hard limits agreed with the owner. A request can only tighten these.
MAX_AMOUNT_LAMPORTS = 1_000_000
MAX_SLIPPAGE_BPS = 50
MAX_PRIORITY_FEE_LAMPORTS = 10_000

WSOL = pubkey("So11111111111111111111111111111111111111112")
USDC = pubkey("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
SYSTEM = pubkey("11111111111111111111111111111111")
COMPUTE_BUDGET = pubkey("ComputeBudget111111111111111111111111111111")
TOKEN = pubkey("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
ATA = pubkey("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
JUPITER = pubkey("JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4")
JUPITER_EVENT_AUTHORITY = pubkey("D8cy77BBepLMngZx6ZukaTff5hCt1HrWyKk3Hnd9oitf")
ALT_PROGRAM = pubkey("AddressLookupTab1e1111111111111111111111111")

# AMM programs accepted as the single direct hop. The hop's CPI data is not
# decoded offline; the program must at least be one of these known venues.
AMM_PROGRAMS = {
    pubkey("whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"): "Orca Whirlpool",
    pubkey("CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK"): "Raydium CLMM",
    pubkey("675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"): "Raydium AMM v4",
    pubkey("CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C"): "Raydium CPMM",
    pubkey("LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo"): "Meteora DLMM",
    pubkey("PhoeNiXZ8ByJGLkxNfZRnkUfjvmuYqLR89jjFHGqdXY"): "Phoenix",
    pubkey("BiSoNHVpsVZW2F7rx2eQ59yQwKxzU5NvBcmKshCSUypi"): "BisonFi",
    pubkey("MNFSTqtC93rEfYHB6hF82sKdZpUDFWkViLByLd1k1Ms"): "Manifest",
}

# Anchor discriminator sha256("global:route_v2")[:8].
ROUTE_V2 = bytes([187, 100, 250, 204, 49, 196, 175, 20])
ROUTE_V2_FIXED = 34  # disc 8 + in 8 + quoted out 8 + slippage 2 + platform fee 2 + positive slippage 2 + vec len 4

BUILTINS = {SYSTEM, COMPUTE_BUDGET, ALT_PROGRAM}
DEFAULT_CU_NON_BUILTIN = 200_000
DEFAULT_CU_BUILTIN = 3_000
MAX_CU = 1_400_000
LAMPORTS_PER_SIGNATURE = 5_000

TOKEN_SYNC_NATIVE = 17
TOKEN_CLOSE_ACCOUNT = 9
TOKEN_APPROVE = {4, 13}


@dataclass(frozen=True)
class Intent:
    user: bytes
    input_mint: bytes
    output_mint: bytes
    amount: int
    slippage_bps: int
    max_priority_fee_lamports: int = MAX_PRIORITY_FEE_LAMPORTS


@dataclass(frozen=True)
class TableEvidence:
    address: bytes
    owner: bytes
    data: bytes
    context_slot: int | None


@dataclass(frozen=True)
class Freshness:
    blockhash: bytes
    last_valid_block_height: int
    current_block_height: int


@dataclass(frozen=True)
class Evidence:
    tables: tuple[TableEvidence, ...] = ()
    freshness: Freshness | None = None


@dataclass
class Report:
    reason: str
    parsed: bool = False
    version: str | None = None
    fee_payer: bytes | None = None
    signatures_all_zero: bool | None = None
    semantic_pass: bool = False
    message_sha256: str | None = None
    details: dict = field(default_factory=dict)

    @property
    def kind(self) -> str:
        return "semantic_pass" if self.semantic_pass else "rejected"


class Reject(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def priority_fee_lamports(price_micro_lamports: int, cu_limit: int) -> int:
    """ceil(price * limit / 1_000_000), the documented Solana prioritization fee."""
    return -(-price_micro_lamports * cu_limit // 1_000_000)


def default_cu_limit(programs: list[bytes]) -> int:
    total = sum(DEFAULT_CU_BUILTIN if program in BUILTINS else DEFAULT_CU_NON_BUILTIN for program in programs)
    return min(total, MAX_CU)


def check_intent(intent: Intent) -> None:
    if intent.input_mint != WSOL or intent.output_mint != USDC:
        raise Reject("unsupported_pair")
    if type(intent.amount) is not int or not 0 < intent.amount <= MAX_AMOUNT_LAMPORTS:
        raise Reject("amount_over_limit")
    if type(intent.slippage_bps) is not int or not 0 <= intent.slippage_bps <= MAX_SLIPPAGE_BPS:
        raise Reject("slippage_over_limit")
    cap = intent.max_priority_fee_lamports
    if type(cap) is not int or not 0 <= cap <= MAX_PRIORITY_FEE_LAMPORTS:
        raise Reject("priority_fee_over_limit")


def resolve_accounts(tx: Transaction, evidence: Evidence) -> tuple[list[bytes], set[int], dict]:
    """Return the full account list, writable indexes, and an ALT summary."""
    keys = list(tx.account_keys)
    n = len(keys)
    signed = tx.num_required_signatures
    writable = set(range(signed - tx.num_readonly_signed))
    writable |= set(range(signed, n - tx.num_readonly_unsigned))
    by_address = {}
    for item in evidence.tables:
        if item.address in by_address:
            raise Reject("alt_layout")
        by_address[item.address] = item
    loaded_w, loaded_r = [], []
    summary = []
    for lookup in tx.lookups:
        item = by_address.get(lookup.table)
        if item is None:
            raise Reject("alt_missing")
        reason, table = parse_table(item.data, item.owner)
        if reason:
            raise Reject(reason)
        reason, usable = usable_count(table, item.context_slot)
        if reason:
            raise Reject(reason)
        for index in lookup.writable + lookup.readonly:
            if index >= usable:
                raise Reject("alt_index")
        loaded_w += [table.addresses[i] for i in lookup.writable]
        loaded_r += [table.addresses[i] for i in lookup.readonly]
        summary.append({"table": b58encode(lookup.table), "addresses": len(table.addresses), "writable": len(lookup.writable), "readonly": len(lookup.readonly)})
    writable |= set(range(n, n + len(loaded_w)))
    full = keys + loaded_w + loaded_r
    if len(set(full)) != len(full):
        raise Reject("duplicate_account")
    return full, writable, {"tables": summary, "loaded_writable": len(loaded_w), "loaded_readonly": len(loaded_r)}


def _u64(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 8], "little")


def _u16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "little")


def check_semantics(tx: Transaction, full: list[bytes], writable: set[int], intent: Intent) -> dict:
    user = intent.user
    wsol_ata = associated_token_address(user, WSOL, TOKEN, ATA)
    usdc_ata = associated_token_address(user, USDC, TOKEN, ATA)
    cu_price = cu_limit = None
    programs = []
    created = []  # (order, ata)
    transfers = sync = route = close = None
    route_fields = {}
    for order, ix in enumerate(tx.instructions):
        if ix.program_index >= len(full) or any(a >= len(full) for a in ix.accounts):
            raise Reject("account_index")
        if ix.program_index == 0 or ix.program_index >= len(tx.account_keys):
            raise Reject("unknown_program")  # programs must be static, non-payer keys
        program = full[ix.program_index]
        programs.append(program)
        accounts = [full[a] for a in ix.accounts]
        data = ix.data
        if program == COMPUTE_BUDGET:
            tag = data[:1]
            if tag == b"\x02" and len(data) == 5:
                if cu_limit is not None:
                    raise Reject("duplicate_compute_budget")
                cu_limit = int.from_bytes(data[1:5], "little")
            elif tag == b"\x03" and len(data) == 9:
                if cu_price is not None:
                    raise Reject("duplicate_compute_budget")
                cu_price = _u64(data, 1)
            elif tag == b"\x04" and len(data) == 5:
                pass  # SetLoadedAccountsDataSizeLimit: no fee or account effect
            else:
                raise Reject("unsupported_instruction")
            if accounts:
                raise Reject("unsupported_instruction")
        elif program == ATA:
            if data not in (b"", b"\x00", b"\x01") or len(accounts) != 6:
                raise Reject("unsupported_instruction")
            payer, ata, owner, mint, system, token_program = accounts
            if payer != user or owner != user or system != SYSTEM or token_program != TOKEN:
                raise Reject("unsupported_intermediate_account")
            if mint == WSOL and ata == wsol_ata:
                created.append((order, ata))
            elif mint == USDC and ata == usdc_ata:
                created.append((order, ata))
            else:
                raise Reject("unsupported_intermediate_account")
        elif program == SYSTEM:
            tag = int.from_bytes(data[:4], "little") if len(data) >= 4 else None
            if tag in (0, 3):
                raise Reject("unsupported_intermediate_account")
            if tag != 2 or len(data) != 12 or len(accounts) != 2:
                raise Reject("unsupported_instruction")
            if transfers is not None:
                raise Reject("unexpected_transfer")
            src, dst = accounts
            if src != user or dst != wsol_ata:
                raise Reject("unexpected_transfer")
            if _u64(data, 4) != intent.amount:
                raise Reject("amount_mismatch")
            transfers = order
        elif program == TOKEN:
            tag = data[0] if data else None
            if tag in TOKEN_APPROVE:
                raise Reject("approve_forbidden")
            if tag in (1, 16, 18):  # InitializeAccount, 2, 3
                raise Reject("unsupported_intermediate_account")
            if tag == TOKEN_SYNC_NATIVE and len(data) == 1 and accounts == [wsol_ata]:
                if sync is not None:
                    raise Reject("unsupported_instruction")
                sync = order
            elif tag == TOKEN_CLOSE_ACCOUNT and len(data) == 1 and accounts == [wsol_ata, user, user]:
                if close is not None:
                    raise Reject("unsupported_instruction")
                close = order
            else:
                raise Reject("unsupported_instruction")
        elif program == JUPITER:
            if data[:8] != ROUTE_V2:
                raise Reject("unsupported_route")
            if route is not None:
                raise Reject("unsupported_route")
            route_fields = _check_route_v2(data, accounts, ix.accounts, writable, intent, wsol_ata, usdc_ata)
            route = order
        else:
            raise Reject("unknown_program")

    price_source = "explicit" if cu_price is not None else "absent"
    cu_price = cu_price or 0
    for name, value in (("transfer", transfers), ("sync", sync), ("route", route), ("close", close)):
        if value is None:
            raise Reject(f"missing_{name}")
    wsol_create = [o for o, ata in created if ata == wsol_ata]
    usdc_create = [o for o, ata in created if ata == usdc_ata]
    if len(wsol_create) != 1 or len(usdc_create) > 1:
        raise Reject("unsupported_intermediate_account")
    if not wsol_create[0] < transfers < sync < route < close:
        raise Reject("instruction_order")
    if usdc_create and usdc_create[0] > route:
        raise Reject("instruction_order")

    limit_source = "explicit" if cu_limit is not None else "default"
    limit = min(cu_limit, MAX_CU) if cu_limit is not None else default_cu_limit(programs)
    priority = priority_fee_lamports(cu_price, limit)
    if priority > intent.max_priority_fee_lamports:
        raise Reject("priority_fee_over_limit")
    base = LAMPORTS_PER_SIGNATURE * tx.num_required_signatures
    return {
        "swap": route_fields,
        "fees": {
            "base_fee_lamports": base,
            "compute_unit_price_micro_lamports": cu_price,
            "compute_unit_price_source": price_source,
            "compute_unit_limit": limit,
            "compute_unit_limit_source": limit_source,
            "priority_fee_lamports": priority,
            "priority_fee_cap_lamports": intent.max_priority_fee_lamports,
            "network_fee_lamports": base + priority,
            "tip_lamports": 0,
        },
        "accounts": {
            "wsol_ata": b58encode(wsol_ata),
            "wsol_ata_created_and_closed_in_tx": True,
            "usdc_ata": b58encode(usdc_ata),
            "usdc_destination_ata_create_idempotent": bool(usdc_create),
            "intermediate_accounts": 0,
        },
    }


def _check_route_v2(data, accounts, indexes, writable, intent, wsol_ata, usdc_ata) -> dict:
    if len(data) < ROUTE_V2_FIXED + 4 or len(accounts) < 10:
        raise Reject("unsupported_route")
    in_amount = _u64(data, 8)
    quoted_out = _u64(data, 16)
    slippage = _u16(data, 24)
    platform_fee = _u16(data, 26)
    positive_slippage = _u16(data, 28)
    steps = int.from_bytes(data[30:34], "little")
    if in_amount != intent.amount:
        raise Reject("amount_mismatch")
    if slippage > MAX_SLIPPAGE_BPS:
        raise Reject("slippage_over_limit")
    if slippage != intent.slippage_bps:
        raise Reject("slippage_mismatch")
    if platform_fee != 0 or positive_slippage != 0:
        raise Reject("platform_fee")
    if quoted_out == 0:
        raise Reject("quoted_out_zero")
    # Direct route only: one step that moves 100% from input slot 0. A step's
    # trailing 4 bytes are bps u16, input_index u8, output_index u8.
    if steps != 1 or _u16(data, len(data) - 4) != 10_000 or data[len(data) - 2] != 0:
        raise Reject("unsupported_route")
    authority, source, destination, src_mint, dst_mint, src_prog, dst_prog, dest_opt, event_auth, program = accounts[:10]
    if authority != intent.user:
        raise Reject("route_authority")
    if source != wsol_ata or destination != usdc_ata:
        raise Reject("route_accounts")
    if src_mint != intent.input_mint or dst_mint != intent.output_mint:
        raise Reject("mint_mismatch")
    if src_prog != TOKEN or dst_prog != TOKEN:
        raise Reject("route_accounts")
    if dest_opt not in (JUPITER, usdc_ata) or event_auth != JUPITER_EVENT_AUTHORITY or program != JUPITER:
        raise Reject("route_accounts")
    if indexes[1] not in writable or indexes[2] not in writable:
        raise Reject("route_accounts")
    venues = sorted({AMM_PROGRAMS[a] for a in accounts[10:] if a in AMM_PROGRAMS})
    if len(venues) != 1:
        raise Reject("unsupported_amm")
    min_out = quoted_out * (10_000 - slippage) // 10_000
    return {
        "instruction": "route_v2",
        "in_amount": in_amount,
        "quoted_out_amount": quoted_out,
        "min_out_amount_floor": min_out,
        "slippage_bps": slippage,
        "platform_fee_bps": platform_fee,
        "route_steps": steps,
        "direct_route": True,
        "amm_program": venues[0],
        "amm_cpi_data_decoded": False,
        "input_mint": b58encode(src_mint),
        "output_mint": b58encode(dst_mint),
    }


def preflight(raw: bytes, intent: Intent, evidence: Evidence) -> Report:
    report = Report("truncated")
    try:
        tx = parse_transaction(raw)
    except WireError as exc:
        report.reason = exc.reason
        report.version = exc.version
        return report
    report.parsed = True
    report.version = tx.version
    report.message_sha256 = hashlib.sha256(tx.message).hexdigest()
    try:
        if len(tx.signatures) != tx.num_required_signatures:
            raise Reject("signature_count")
        report.signatures_all_zero = all(sig == bytes(64) for sig in tx.signatures)
        if not tx.account_keys:
            raise Reject("truncated")
        report.fee_payer = tx.account_keys[0]
        if tx.version != "v0":
            raise Reject("unsupported_version")
        if tx.num_required_signatures != 1 or tx.num_readonly_signed != 0:
            raise Reject("signer_layout")
        if not report.signatures_all_zero:
            raise Reject("signature_present")
        check_intent(intent)
        if report.fee_payer != intent.user:
            raise Reject("fee_payer")
        full, writable, alt_summary = resolve_accounts(tx, evidence)
        report.details["alt"] = alt_summary
        report.details.update(check_semantics(tx, full, writable, intent))
        fresh = evidence.freshness
        if fresh is None:
            raise Reject("freshness_missing")
        if fresh.blockhash != tx.recent_blockhash:
            raise Reject("blockhash_mismatch")
        blocks_left = fresh.last_valid_block_height - fresh.current_block_height
        report.details["freshness"] = {
            "blockhash": b58encode(tx.recent_blockhash),
            "last_valid_block_height": fresh.last_valid_block_height,
            "current_block_height": fresh.current_block_height,
            "blocks_left": blocks_left,
        }
        if blocks_left < 0:
            raise Reject("stale_blockhash")
    except Reject as exc:
        report.reason = exc.reason
        return report
    report.semantic_pass = True
    report.reason = "ok_unsigned"
    return report
