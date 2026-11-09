"""Program-derived addresses and associated token accounts, standard library only.

A PDA is sha256(seeds || bump || program_id || "ProgramDerivedAddress") that is
not a valid compressed Ed25519 point. The point test follows RFC 8032 section
5.1.3 decoding (square-root existence for x), matching curve25519-dalek's
decompress, which ignores the sign bit for this purpose.
"""

import hashlib

P = 2**255 - 19
D = (-121665 * pow(121666, P - 2, P)) % P
SQRT_M1 = pow(2, (P - 1) // 4, P)


def is_on_curve(point: bytes) -> bool:
    if len(point) != 32:
        return False
    y = int.from_bytes(point, "little") & ((1 << 255) - 1)
    y %= P
    u = (y * y - 1) % P
    v = (D * y * y + 1) % P
    x = (u * pow(v, 3, P) * pow(u * pow(v, 7, P), (P - 5) // 8, P)) % P
    check = (v * x * x) % P
    return check == u or check == (-u) % P


def create_program_address(seeds: list[bytes], program_id: bytes) -> bytes | None:
    digest = hashlib.sha256(b"".join(seeds) + program_id + b"ProgramDerivedAddress").digest()
    if is_on_curve(digest):
        return None
    return digest


def find_program_address(seeds: list[bytes], program_id: bytes) -> tuple[bytes, int]:
    for bump in range(255, -1, -1):
        address = create_program_address(seeds + [bytes([bump])], program_id)
        if address is not None:
            return address, bump
    raise ValueError("no viable bump")


def associated_token_address(owner: bytes, mint: bytes, token_program: bytes, ata_program: bytes) -> bytes:
    return find_program_address([owner, token_program, mint], ata_program)[0]
