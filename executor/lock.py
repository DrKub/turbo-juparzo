import fcntl
import os
from contextlib import contextmanager
from pathlib import Path


class LockBusy(Exception):
    pass


class LockError(Exception):
    """The lock path is not a stable file this process can own.

    A caller who can unlink files inside the state directory can still replace
    the lock inode after this check. The boundary is the directory's permissions,
    not a claim that one stat prevents every later replacement.
    """


def _refuse_symlinks(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise LockError("symlink refused")


@contextmanager
def exclusive_lock(path: Path):
    absolute = Path(os.path.abspath(path))
    _refuse_symlinks(absolute)
    absolute.parent.mkdir(parents=True, exist_ok=True)
    if absolute.parent.is_symlink():
        raise LockError("symlink refused")
    directory = os.open(absolute.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    descriptor = None
    try:
        descriptor = os.open(
            absolute.name,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=directory,
        )
        held = os.fstat(descriptor)
        listed = os.stat(absolute, follow_symlinks=False)
        if (held.st_dev, held.st_ino) != (listed.st_dev, listed.st_ino):
            raise LockError("lock identity changed")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LockBusy() from exc
        yield descriptor
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)
