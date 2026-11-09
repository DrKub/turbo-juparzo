"""Bitcoin-alphabet base58, as used for Solana addresses. Standard library only."""

ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_INDEX = {char: index for index, char in enumerate(ALPHABET)}


def b58encode(raw: bytes) -> str:
    number = int.from_bytes(raw, "big")
    out = []
    while number:
        number, rem = divmod(number, 58)
        out.append(ALPHABET[rem])
    pad = len(raw) - len(raw.lstrip(b"\0"))
    return "1" * pad + "".join(reversed(out))


def b58decode(text: str) -> bytes:
    if not isinstance(text, str) or text == "":
        raise ValueError("empty base58")
    number = 0
    for char in text:
        if char not in _INDEX:
            raise ValueError("invalid base58 character")
        number = number * 58 + _INDEX[char]
    body = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    pad = len(text) - len(text.lstrip("1"))
    return b"\0" * pad + body


def pubkey(text: str) -> bytes:
    """Decode a base58 address and require exactly 32 bytes."""
    raw = b58decode(text)
    if len(raw) != 32:
        raise ValueError("address must decode to 32 bytes")
    return raw
