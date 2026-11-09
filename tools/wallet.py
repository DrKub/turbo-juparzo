"""Wallet profile loader: the single source of the signing address.

config/wallet.json holds named profiles ("phantom" = 8Xm6..., default; "turnkey" = 9Ao3...).
Selection order: explicit name argument, env TURBO_WALLET, default_profile. Each profile's
address must decode to 32 bytes and lie on the ed25519 curve, and its recorded WSOL/USDC
associated token accounts must equal the ones recomputed with metis_offline.pda.
"""

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from metis_offline.base58 import b58encode, pubkey  # noqa: E402
from metis_offline.pda import associated_token_address, is_on_curve  # noqa: E402
from metis_offline.preflight import ATA, TOKEN, USDC, WSOL  # noqa: E402

CONFIG_PATH = ROOT / "config" / "wallet.json"
ENV_VAR = "TURBO_WALLET"


class WalletConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Profile:
    name: str
    address: str
    signer: str
    wsol_ata: str
    usdc_ata: str


def _check(name: str, entry: dict) -> Profile:
    try:
        address = entry["address"]
        key = pubkey(address)
    except Exception:
        raise WalletConfigError(f"{name}: bad address")
    if not is_on_curve(key):
        raise WalletConfigError(f"{name}: address not on ed25519 curve")
    wsol = b58encode(associated_token_address(key, WSOL, TOKEN, ATA))
    usdc = b58encode(associated_token_address(key, USDC, TOKEN, ATA))
    if entry.get("wsol_ata") != wsol or entry.get("usdc_ata") != usdc:
        raise WalletConfigError(f"{name}: recorded ATA mismatch")
    if entry.get("signer") not in ("phantom_embedded", "turnkey"):
        raise WalletConfigError(f"{name}: unknown signer")
    return Profile(name, address, entry["signer"], wsol, usdc)


def load_profile(name: str | None = None, path: Path = CONFIG_PATH, env=os.environ) -> Profile:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise WalletConfigError("wallet config unreadable")
    chosen = name or env.get(ENV_VAR) or doc.get("default_profile")
    profiles = doc.get("profiles") or {}
    if chosen not in profiles:
        raise WalletConfigError(f"unknown wallet profile {chosen!r}")
    return _check(chosen, profiles[chosen])
