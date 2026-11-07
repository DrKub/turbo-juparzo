import re

_NONNEG = re.compile(r"^(0|[1-9][0-9]*)$")
_POS = re.compile(r"^[1-9][0-9]*$")
_SIZE = re.compile(r"^(0|[1-9][0-9]*)(?:\.([0-9]{1,9}))?$")


def parse_nonneg(value: object) -> int | None:
    if not isinstance(value, str) or _NONNEG.fullmatch(value) is None:
        return None
    return int(value)


def parse_pos(value: object) -> int | None:
    if not isinstance(value, str) or _POS.fullmatch(value) is None:
        return None
    return int(value)


def parse_size_lamports(value: object) -> int | None:
    if not isinstance(value, str) or _SIZE.fullmatch(value) is None:
        return None
    whole, frac = value.split(".") if "." in value else (value, "")
    lamports = int(whole) * 1_000_000_000 + int(frac.ljust(9, "0") or "0")
    if lamports <= 0:
        return None
    return lamports
