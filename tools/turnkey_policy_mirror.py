"""Local mirror of the Turnkey policy 1 (bounded Jupiter SOL->USDC swap) and the deny policies.

It evaluates the same facts Turnkey's `solana.tx` exposes (config/turnkey/policies.json) on a parsed
transaction BEFORE any request is sent, so a transaction Turnkey would deny never leaves the box and
a mirror/policy drift shows up as a local stop. It is a prediction, not the enforcement: Turnkey's
engine decides. It is deliberately a hand-written mirror (no policy-language evaluator); a test pins
its constants to policies.json.

Facts as Turnkey defines them: only TOP-LEVEL System Transfer (tag 2) and Token Transfer(3) /
TransferChecked(12) count as transfers / spl_transfers; accounts resolved through an address lookup
table appear as the literal ADDRESS_TABLE_LOOKUP.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from metis_offline.base58 import b58encode  # noqa: E402

LOOKUP = "ADDRESS_TABLE_LOOKUP"
SYSTEM = "11111111111111111111111111111111"
TOKEN = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
ATA = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
COMPUTE_BUDGET = "ComputeBudget111111111111111111111111111111"
JUPITER = "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"
ALLOWED_PROGRAMS = (COMPUTE_BUDGET, SYSTEM, TOKEN, ATA, JUPITER)
ROUTE_V2_PREFIX_HEX = "bb64facc31c4af14"
MAX_INSTRUCTIONS = 8
MAX_TRANSFER_LAMPORTS = 1_000_000
OWNER_WITHDRAW = "3YxVKCTSZUvQGFPL51dzXPoFj82k4v4NJC6cSxDuyqwx"


def facts(tx) -> dict:
    """Turnkey-style view of a parsed transaction (metis_offline.wire.Transaction)."""
    keys = [b58encode(k) for k in tx.account_keys]

    def key(index: int) -> str:
        return keys[index] if index < len(keys) else LOOKUP

    instructions, transfers, spl_transfers = [], [], []
    for ix in tx.instructions:
        program = key(ix.program_index)
        accounts = [key(a) for a in ix.accounts]
        instructions.append({"program": program, "accounts": accounts, "data_hex": ix.data.hex()})
        if program == SYSTEM and ix.data[:4] == b"\x02\x00\x00\x00" and len(ix.data) >= 12 and len(accounts) >= 2:
            transfers.append({"from": accounts[0], "to": accounts[1], "amount": int.from_bytes(ix.data[4:12], "little")})
        elif program in (TOKEN, TOKEN_2022) and ix.data[:1] in (b"\x03", b"\x0c"):
            spl_transfers.append({"program": program})
    return {"version": {"legacy": "LEGACY", "v0": "V0"}.get(tx.version, str(tx.version).upper()), "instructions": instructions,
            "program_keys": sorted({i["program"] for i in instructions}), "transfers": transfers, "spl_transfers": spl_transfers,
            "address_table_lookups": len(tx.lookups)}


def swap_failures(tx, signer: str, wsol_ata: str, usdc_ata: str) -> list[str]:
    """Reasons policy 1 would NOT allow TX (empty list = it matches). Mirrors config/turnkey/policies.json policy 1."""
    f = facts(tx)
    bad = []
    if f["version"] != "V0":
        bad.append("version_not_v0")
    for program in f["program_keys"]:
        if program not in ALLOWED_PROGRAMS:
            bad.append(f"program_not_allowed:{program}")
    if len(f["instructions"]) > MAX_INSTRUCTIONS:
        bad.append("too_many_instructions")
    if f["spl_transfers"]:
        bad.append("spl_transfer_present")
    if len(f["transfers"]) != 1:
        bad.append("transfer_count_not_1")
    for t in f["transfers"]:
        if t["from"] != signer or t["to"] != wsol_ata or not 0 < t["amount"] <= MAX_TRANSFER_LAMPORTS:
            bad.append("transfer_not_signer_to_wsol_ata_within_cap")
    jup = [i for i in f["instructions"] if i["program"] == JUPITER]
    if len(jup) != 1:
        bad.append("jupiter_instruction_count_not_1")
    for i in jup:
        if i["data_hex"][:16] != ROUTE_V2_PREFIX_HEX:
            bad.append("jupiter_not_route_v2")
        if i["accounts"][:3] != [signer, wsol_ata, usdc_ata]:
            bad.append("jupiter_accounts_not_signer_wsol_usdc")
    # explicit deny policies (belt)
    if any(t["to"] not in (wsol_ata, OWNER_WITHDRAW) for t in f["transfers"]):
        bad.append("deny_transfer_to_other_destination")
    return sorted(set(bad))
