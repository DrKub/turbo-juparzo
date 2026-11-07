import json
import sys
from pathlib import Path

from executor.book import fold
from executor.journal import JournalError, commit_record, discard_temps, load_records, mark_path, receipt_path
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


def _same_signal(left: Signal, right: Signal) -> bool:
    return (
        left.id == right.id
        and left.side == right.side
        and left.mint == right.mint
        and left.created_at == right.created_at
        and left.size_lamports == right.size_lamports
        and left.token_amount == right.token_amount
    )


def _candidates(pending: list[Signal | SchemaRejection]):
    groups: dict[tuple[str, str], dict] = {}
    order: list[tuple[str, str]] = []
    for item in pending:
        if isinstance(item, Signal):
            key = (item.id, item.side)
        elif isinstance(item, SchemaRejection) and item.id and item.side in {"buy", "sell"}:
            key = (item.id, item.side)
        else:
            continue
        if key not in groups:
            order.append(key)
            groups[key] = {"valid": [], "bad": False}
        if isinstance(item, Signal):
            groups[key]["valid"].append(item)
        else:
            groups[key]["bad"] = True
    signals: list[Signal] = []
    rejections: list[tuple[str, str]] = []
    for key in order:
        group = groups[key]
        valids = group["valid"]
        if len(valids) > 1 and any(not _same_signal(valids[0], other) for other in valids[1:]):
            return "conflict", [], []
        if valids:
            signals.append(valids[0])
        elif group["bad"]:
            rejections.append(key)
    signals.sort(key=lambda item: item.sort_key)
    return "ok", signals, rejections


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
    try:
        return _locked(config, inbox, state, transport, flag, crashes)
    except JournalError:
        print("journal corrupt", file=sys.stderr)
        return 4


def _locked(config, inbox, state, transport, flag, crashes) -> int:
    with exclusive_lock(state / "executor.lock"):
        discard_temps(state)
        status, valid, rejections = _candidates(_signals(inbox))
        if status == "conflict":
            print("inbox conflict", file=sys.stderr)
            return 4
        for signal_id, side in rejections:
            receipts, marks, _book = _refold(config, state)
            if any(record.id == signal_id and record.side == side for record in receipts):
                continue
            _commit(
                state,
                Receipt(
                    signal_id,
                    side,
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
