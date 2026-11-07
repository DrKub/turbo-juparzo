import json
from pathlib import Path

from executor.integers import parse_nonneg, parse_pos
from executor.types import BuyEstimate, DryRunConfig

_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_CLOSED = {"bank_lamports", "mint_metadata", "denylist_mints", "buy_estimates"}


class ConfigError(Exception):
    pass


def is_mint(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if any(char not in _ALPHABET for char in value):
        return False
    number = 0
    for char in value:
        number = number * 58 + _ALPHABET.index(char)
    pad = 0
    for char in value:
        if char != "1":
            break
        pad += 1
    raw = b"" if number == 0 else number.to_bytes((number.bit_length() + 7) // 8, "big")
    return len(b"\x00" * pad + raw) == 32


def load_config(path: Path) -> DryRunConfig:
    body = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(body, dict) or not set(body).issubset(_CLOSED):
        raise ConfigError("refused: config")
    if "bank_lamports" not in body:
        raise ConfigError("refused: config")
    bank = parse_pos(body["bank_lamports"])
    if bank is None:
        raise ConfigError("refused: config")
    metadata: dict[str, int] = {}
    for item in body.get("mint_metadata", []):
        if not isinstance(item, dict) or set(item) != {"mint", "decimals"}:
            raise ConfigError("refused: config")
        decimals = item["decimals"]
        if isinstance(decimals, bool) or not isinstance(decimals, int) or not 0 <= decimals <= 255:
            raise ConfigError("refused: config")
        if not is_mint(item["mint"]) or item["mint"] in metadata:
            raise ConfigError("refused: config")
        metadata[item["mint"]] = decimals
    denylist: tuple[str, ...] | None = None
    if "denylist_mints" in body:
        raw = body["denylist_mints"]
        if not isinstance(raw, list) or not all(is_mint(item) for item in raw):
            raise ConfigError("refused: config")
        denylist = tuple(raw)
    estimates: dict[str, BuyEstimate] = {}
    raw_estimates = body.get("buy_estimates", {})
    if not isinstance(raw_estimates, dict):
        raise ConfigError("refused: config")
    for signal_id, item in raw_estimates.items():
        if not isinstance(item, dict) or set(item) != {
            "estimated_fee_lamports",
            "estimated_rent_lamports",
        }:
            raise ConfigError("refused: config")
        fee = parse_nonneg(item["estimated_fee_lamports"])
        rent = parse_nonneg(item["estimated_rent_lamports"])
        if fee is None or rent is None:
            raise ConfigError("refused: config")
        estimates[signal_id] = BuyEstimate(fee, rent)
    return DryRunConfig(bank, metadata, denylist, estimates)
