import errno
import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from spaceten.errors import WorldLocked


def lock_enabled() -> bool:
    return os.environ.get("SPACETEN_LOCK", "1") != "0"


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    """LOCK_EX|LOCK_NB: fail immediately if another process holds the lock."""
    if not lock_enabled():
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK}:
                raise WorldLocked("world is locked") from exc
            raise
        yield
    finally:
        handle.close()
