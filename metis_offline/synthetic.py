"""Build the synthetic unsigned v0 fixture with solders.

Wire layout, after standard base64 decoding:

- compact-u16 signature count: 1
- one 64-byte signature, every byte 0
- message version prefix: 0x80, which means v0
- header: 1 required signature, 0 readonly signed accounts, 1 readonly unsigned account
- compact-u16 static account count: 2
- fee payer: 32 bytes of 0x01
- program id: 32 bytes of 0x02
- recent blockhash: 32 bytes of 0x00
- compact-u16 instruction count: 1
- instruction: program index 1, no accounts, no data
- compact-u16 lookup count: 1
- lookup table account: 32 bytes of 0x03
- writable lookup indexes: one byte, value 7
- readonly lookup indexes: empty

These accounts are repeated byte patterns. They are not wallets, programs, or lookup tables on any network. The zero blockhash is not a recent blockhash, so the bytes are not broadcastable.
"""

import base64

from solders.hash import Hash
from solders.instruction import CompiledInstruction
from solders.message import MessageAddressTableLookup, MessageHeader, MessageV0
from solders.null_signer import NullSigner
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from metis_offline.fixtures import FEE_PAYER, PROGRAM_ID, TABLE_ID


def build_v0_b64() -> str:
    payer = Pubkey.from_bytes(FEE_PAYER)
    message = MessageV0(
        MessageHeader(1, 0, 1),
        [payer, Pubkey.from_bytes(PROGRAM_ID)],
        Hash.default(),
        [CompiledInstruction(1, b"", bytes())],
        [MessageAddressTableLookup(Pubkey.from_bytes(TABLE_ID), bytes([7]), bytes())],
    )
    transaction = VersionedTransaction(message, [NullSigner(payer)])
    return base64.b64encode(bytes(transaction)).decode("ascii")
