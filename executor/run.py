import json
from pathlib import Path

from executor.book import fold
from executor.journal import commit_record, discard_temps, load_records, mark_path, receipt_path
from executor.lock import exclusive_lock
from executor.marks import decide_mark
from executor.quote import quote_once
from executor.records import mark_payload, receipt_payload
from executor.risk import denylist_label, prequote
from executor.schema import log_schema, parse_signal
from executor.types import (
    SOL_MINT,
    DryRunConfig,
    MarkRecord,
    QuoteRequest,
    QuoteSuccess,
    QuoteUnavailable,
    QuoteUnsafe,
    Receipt,
    SchemaRejection,
    Signal,
)


class StopFlag:
    def __init__(self):
        self._set = False

    def set(self):
        self._set = True

    def is_set(self):
        return self._set


def _signals(inbox: Path) -> list[Signal | SchemaRejection]:
    parsed: list[Signal | SchemaRejection] = []
    if not inbox.exists():
        return parsed
    for path in sorted(item for item in inbox.iterdir() if item.is_file() and item.suffix == ".json"):
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log_schema(SchemaRejection(None, False, None))
            continue
        item = parse_signal(body)
        if isinstance(item, SchemaRejection):
            log_schema(item)
        parsed.append(item)
    return parsed


def _refold(config: DryRunConfig, state: Path):
    receipts, marks = load_records(state)
    return receipts, marks, fold(config.bank_lamports, receipts, marks)


def _next_seq(receipts, marks) -> int:
    seen = [item.seq for item in receipts] + [item.seq for item in marks]
    return max(seen, default=0) + 1


def _commit(state: Path, receipt: Receipt, crash_commits: list[int]):
    crash = crash_commits[0] > 0
    if crash:
        crash_commits[0] -= 1
    commit_record(state, receipt_path(receipt.id, receipt.side), receipt_payload(receipt), crash=crash)


def _apply_marks(config, state, transport, flag, crashes):
    _receipts, _marks, book = _refold(config, state)
    for mint in sorted(book.positions):
        if flag.is_set():
            return
        receipts, marks, book = _refold(config, state)
        position = book.positions.get(mint)
        if position is None:
            continue
        result = quote_once(transport, QuoteRequest(position.mint, SOL_MINT, str(position.token_base_units)))
        if flag.is_set():
            return
        if isinstance(result, (QuoteUnavailable, QuoteUnsafe)):
            continue
        if not isinstance(result, QuoteSuccess):
            continue
        decision = decide_mark(position, int(result.quote.out_amount), int(result.quote.in_amount))
        seq = _next_seq(receipts, marks)
        if decision == "observe":
            number = max((item.n for item in marks if item.buy_id == position.buy_id), default=0) + 1
            commit_record(
                state,
                mark_path(position.buy_id, number),
                mark_payload(
                    MarkRecord(
                        position.buy_id,
                        number,
                        int(result.quote.out_amount),
                        int(result.quote.in_amount),
                        seq,
                        result.quote,
                    )
                ),
                crash=False,
            )
            continue
        _commit(
            state,
            Receipt(
                f"rexit.{position.buy_id}.{decision}",
                "sell",
                "quoted",
                decision,
                position.mint,
                result.quote.in_amount,
                result.quote.out_amount,
                None,
                denylist_label(config),
                seq,
                None,
                None,
                None,
                result.quote,
            ),
            crashes,
        )


def run(
    config: DryRunConfig,
    inbox: Path,
    state: Path,
    transport,
    *,
    crash_commits: int = 0,
    stop: StopFlag | None = None,
) -> int:
    flag = stop or StopFlag()
    crashes = [crash_commits]
    with exclusive_lock(state / "executor.lock"):
        discard_temps(state)
        pending = _signals(inbox)
        for item in pending:
            if not isinstance(item, SchemaRejection) or not item.log_id or item.id is None or item.side not in {"buy", "sell"}:
                continue
            receipts, marks, _book = _refold(config, state)
            if any(record.id == item.id and record.side == item.side for record in receipts):
                continue
            _commit(
                state,
                Receipt(
                    item.id,
                    item.side,
                    "rejected",
                    "signal",
                    SOL_MINT,
                    None,
                    None,
                    "schema",
                    denylist_label(config),
                    _next_seq(receipts, marks),
                    None,
                    None,
                    None,
                    None,
                ),
                crashes,
            )
        valid = sorted((item for item in pending if isinstance(item, Signal)), key=lambda item: item.sort_key)
        for signal in valid:
            if flag.is_set():
                return 0
            receipts, marks, book = _refold(config, state)
            if any(record.id == signal.id and record.side == signal.side for record in receipts):
                continue
            reason = prequote(config, book, signal)
            seq = _next_seq(receipts, marks)
            label = denylist_label(config)
            if reason is not None:
                _commit(
                    state,
                    Receipt(
                        signal.id,
                        signal.side,
                        "rejected",
                        "signal",
                        signal.mint,
                        None,
                        None,
                        reason,
                        label,
                        seq,
                        None,
                        None,
                        None,
                        None,
                    ),
                    crashes,
                )
                continue
            if signal.side == "buy":
                request = QuoteRequest(SOL_MINT, signal.mint, str(signal.size_lamports))
            else:
                request = QuoteRequest(signal.mint, SOL_MINT, str(signal.token_amount))
            result = quote_once(transport, request)
            if flag.is_set():
                return 0
            if isinstance(result, QuoteUnavailable):
                continue
            if isinstance(result, QuoteUnsafe):
                _commit(
                    state,
                    Receipt(
                        signal.id,
                        signal.side,
                        "rejected",
                        "signal",
                        signal.mint,
                        None,
                        None,
                        "quote_unsafe",
                        label,
                        seq,
                        None,
                        None,
                        None,
                        None,
                    ),
                    crashes,
                )
                continue
            if not isinstance(result, QuoteSuccess):
                continue
            estimate = config.buy_estimates.get(signal.id)
            _commit(
                state,
                Receipt(
                    signal.id,
                    signal.side,
                    "quoted",
                    "signal",
                    signal.mint,
                    result.quote.in_amount,
                    result.quote.out_amount,
                    None,
                    label,
                    seq,
                    signal.size_lamports if signal.side == "buy" else None,
                    estimate.estimated_fee_lamports if signal.side == "buy" and estimate else None,
                    estimate.estimated_rent_lamports if signal.side == "buy" and estimate else None,
                    result.quote,
                ),
                crashes,
            )
        if flag.is_set():
            return 0
        _apply_marks(config, state, transport, flag, crashes)
        return 0
