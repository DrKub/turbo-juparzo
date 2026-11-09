import json
import os
import re
import stat
from pathlib import Path

from executor.integers import parse_nonneg, parse_pos
from executor.types import MarkRecord, Receipt, StoredQuote

_SIGNAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_EXIT_ID = re.compile(r"^rexit\.[A-Za-z0-9][A-Za-z0-9._-]{0,95}\.(stop|trailing)$")


class CrashBeforeRename(Exception):
    pass


class JournalError(Exception):
    pass


def _journal_id(value: str) -> bool:
    if _EXIT_ID.fullmatch(value):
        return True
    return _SIGNAL_ID.fullmatch(value) is not None and not value.startswith("rexit.")


def receipt_path(signal_id: str, side: str) -> Path:
    if not _journal_id(signal_id) or side not in {"buy", "sell"}:
        raise JournalError("unsafe journal path")
    return Path("journal") / signal_id / f"{side}.json"


def mark_path(buy_id: str, n: int) -> Path:
    if not _SIGNAL_ID.fullmatch(buy_id) or buy_id.startswith("rexit.") or n < 1:
        raise JournalError("unsafe mark path")
    return Path("journal") / buy_id / "marks" / f"{n}.json"


def _contained(root: Path, relative: Path) -> Path:
    if relative.is_absolute() or ".." in relative.parts:
        raise JournalError("unsafe journal path")
    root_resolved = root.resolve()
    target = (root_resolved / relative).resolve()
    if target != root_resolved and root_resolved not in target.parents:
        raise JournalError("unsafe journal path")
    return target


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_up_to_root(root: Path, directory: Path) -> None:
    root_resolved = root.resolve()
    current = directory.resolve()
    chain = []
    while True:
        chain.append(current)
        if current == root_resolved:
            break
        parent = current.parent
        if parent == current or root_resolved not in current.parents:
            raise JournalError("unsafe journal path")
        current = parent
    for item in chain:
        _fsync_directory(item)


def commit_record(root: Path, relative: Path, payload: dict, *, crash: bool = False) -> None:
    target = _contained(root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    _fsync_up_to_root(root, target.parent)
    temporary = target.parent / f".{target.name}.tmp"
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    if crash:
        raise CrashBeforeRename(temporary)
    os.replace(temporary, target)
    _fsync_up_to_root(root, target.parent)


def discard_temps(root: Path) -> None:
    if not root.exists():
        return
    for path in root.rglob("*.tmp"):
        if path.is_file():
            path.unlink()


def _stored_quote(body: object) -> StoredQuote | None:
    if body is None:
        return None
    if not isinstance(body, dict):
        raise JournalError("bad quote")
    return StoredQuote(
        body.get("router") if isinstance(body.get("router"), str) else None,
        body["inAmount"],
        body["outAmount"],
        body.get("feeBps") if isinstance(body.get("feeBps"), int) and not isinstance(body.get("feeBps"), bool) else None,
        body.get("feeMint") if isinstance(body.get("feeMint"), str) else None,
        body.get("platformFee") if isinstance(body.get("platformFee"), dict) else None,
    )


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    parsed = parse_nonneg(value)
    if parsed is None:
        raise JournalError("bad integer")
    return parsed


def _required_pos(value: object) -> int:
    parsed = parse_pos(value)
    if parsed is None:
        raise JournalError("bad seq")
    return parsed


def _regular_file(path: Path) -> os.stat_result:
    if path.is_symlink():
        raise JournalError("journal corrupt")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise JournalError("journal corrupt")
    return info


def load_records(root: Path) -> tuple[list[Receipt], list[MarkRecord]]:
    receipts: list[Receipt] = []
    marks: list[MarkRecord] = []
    journal = root / "journal"
    if not journal.exists():
        return receipts, marks
    if journal.is_symlink():
        raise JournalError("journal corrupt")
    seen_inodes: set[tuple[int, int]] = set()
    paths: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(journal, followlinks=False):
        current = Path(dirpath)
        if current.is_symlink():
            raise JournalError("journal corrupt")
        for dirname in dirnames:
            if (current / dirname).is_symlink():
                raise JournalError("journal corrupt")
        for filename in filenames:
            path = current / filename
            if path.suffix != ".json" or path.name.startswith("."):
                continue
            info = _regular_file(path)
            ident = (info.st_dev, info.st_ino)
            if ident in seen_inodes:
                raise JournalError("journal corrupt")
            seen_inodes.add(ident)
            paths.append(path)
    for path in sorted(paths):
        body = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(body, dict):
            raise JournalError("unknown record")
        relative = path.relative_to(journal)
        if body.get("record_type") == "jupiter_dry_run_receipt":
            if body.get("status") != "dry_run":
                raise JournalError("unknown record")
            expected = Path(str(body.get("id"))) / f"{body.get('side')}.json"
            if relative != expected:
                raise JournalError("journal corrupt")
            receipts.append(
                Receipt(
                    body["id"],
                    body["side"],
                    body["outcome"],
                    body["trigger"],
                    body["mint"],
                    body["quoted_in_amount"],
                    body["quoted_out_amount"],
                    body["reason"],
                    body["denylist"],
                    _required_pos(body["seq"]),
                    _optional_int(body["buy_lamports"]),
                    _optional_int(body["estimated_fee_lamports"]),
                    _optional_int(body["estimated_rent_lamports"]),
                    _stored_quote(body["quote"]),
                )
            )
        elif body.get("record_type") == "jupiter_dry_run_mark":
            expected = Path(str(body.get("buy_id"))) / "marks" / f"{body.get('n')}.json"
            if relative != expected:
                raise JournalError("journal corrupt")
            marks.append(
                MarkRecord(
                    body["buy_id"],
                    _required_pos(body["n"]),
                    _required_pos(body["mark_out_lamports"]),
                    _required_pos(body["mark_in_base_units"]),
                    _required_pos(body["seq"]),
                    _stored_quote(body["quote"]),
                )
            )
        else:
            raise JournalError("unknown record")
    keys = [(item.id, item.side) for item in receipts]
    sequences = [item.seq for item in receipts] + [item.seq for item in marks]
    if len(keys) != len(set(keys)) or len(sequences) != len(set(sequences)):
        raise JournalError("journal corrupt")
    return receipts, marks
