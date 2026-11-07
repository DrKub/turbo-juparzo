import re
import sys
from datetime import datetime, timezone

from executor.config import is_mint
from executor.integers import parse_pos, parse_size_lamports
from executor.types import SchemaRejection, Signal

_SIGNAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_BUY = {"id", "side", "mint", "created_at", "size_sol"}
_SELL = {"id", "side", "mint", "created_at", "token_amount"}


def _safe_id(value: object) -> str | None:
    if isinstance(value, str) and _SIGNAL_ID.fullmatch(value) and not value.startswith("rexit."):
        return value
    return None


def _millis(value: str) -> int | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    delta = parsed.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return ((delta.days * 86400 + delta.seconds) * 1000) + (delta.microseconds // 1000)


def log_schema(rejection: SchemaRejection) -> None:
    if rejection.log_id and rejection.id is not None:
        print(f"schema {rejection.id}", file=sys.stderr)
    else:
        print("schema", file=sys.stderr)


def parse_signal(body: object) -> Signal | SchemaRejection:
    if not isinstance(body, dict):
        return SchemaRejection(None, False, None)
    signal_id = _safe_id(body.get("id"))
    side = body.get("side")
    known_side = side if side in {"buy", "sell"} else None
    allowed = _BUY if side == "buy" else _SELL if side == "sell" else None
    if allowed is None or set(body) != allowed or signal_id is None or not is_mint(body.get("mint")):
        return SchemaRejection(signal_id, signal_id is not None, known_side)
    millis = _millis(body.get("created_at")) if isinstance(body.get("created_at"), str) else None
    if millis is None:
        return SchemaRejection(signal_id, True, known_side)
    if side == "buy":
        lamports = parse_size_lamports(body.get("size_sol"))
        if lamports is None:
            return SchemaRejection(signal_id, True, known_side)
        amount = None
    else:
        amount = parse_pos(body.get("token_amount"))
        if amount is None:
            return SchemaRejection(signal_id, True, known_side)
        lamports = None
    return Signal(
        signal_id,
        side,
        body["mint"],
        body["created_at"],
        (millis, signal_id, 0 if side == "buy" else 1),
        lamports,
        amount,
    )
