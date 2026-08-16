import hashlib
import os
import re
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from threading import Lock

from spaceten.errors import WorldLocked
from spaceten.kernel.energy import AccountView
from spaceten.kernel.event import Act, Event
from spaceten.kernel.number import Id
from spaceten.kernel.space import Cell
from spaceten.store.protocol import WorldHeader

_PART_NAME = re.compile(r"^\.[0-9A-HJKMNP-TV-Z]{26}\.part$")
_RESERVED_DIR = ".spaceten"


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


class MemoryStore:
    """Events/header/caches in RAM; dest bytes always live on the jailed FS."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root.resolve() if root is not None else None
        self._events: list[Event] = []
        self._header: WorldHeader | None = None
        self._caches: tuple[AccountView, dict[str, Cell]] | None = None
        self._mu = Lock()

    def append(self, event: Event) -> None:
        self._events.append(event)

    def load_events(self) -> list[Event]:
        return list(self._events)

    def load_header(self) -> WorldHeader:
        if self._header is None:
            raise FileNotFoundError("world header is missing")
        return self._header

    def save_header(self, header: WorldHeader) -> None:
        self._header = header

    def save_caches(self, account: AccountView, cells: dict[str, Cell]) -> None:
        self._caches = (account, dict(cells))

    def load_caches(self) -> tuple[AccountView, dict[str, Cell]] | None:
        if self._caches is None:
            return None
        account, cells = self._caches
        return account, dict(cells)

    def lock(self) -> AbstractContextManager[None]:
        return _nonblocking_lock(self._mu)

    def stage_write(self, event_id: Id, dest: Path, data: bytes) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        staged = dest.parent / f".{event_id}.part"
        with open(staged, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        return staged

    def commit_write(self, staged: Path, dest: Path) -> None:
        os.replace(staged, dest)

    def recover_writes(self, events: list[Event]) -> None:
        if self.root is None:
            return
        acts = {event.id: event for event in events if isinstance(event.op, Act)}
        seen: set[Path] = set()
        act_dests: set[Path] = set()
        for event in acts.values():
            dest = self._act_dest(event)
            act_dests.add(_resolved(dest))
            staged = dest.parent / f".{event.id}.part"
            seen.add(_resolved(staged))
            self._recover_act(event, dest, staged)
        for staged in self._iter_part_files():
            resolved = _resolved(staged)
            if resolved in seen or resolved in act_dests:
                continue
            event_id = staged.name[1 : -len(".part")]
            matching = acts.get(event_id)
            if matching is None:
                staged.unlink(missing_ok=True)
                continue
            dest = self._act_dest(matching)
            self._recover_act(matching, dest, staged)

    def _act_dest(self, event: Event) -> Path:
        assert self.root is not None
        op = event.op
        if not isinstance(op, Act):
            raise TypeError("recover dest requires an Act event")
        return self.root / op.address

    def _recover_act(self, event: Event, dest: Path, staged: Path) -> None:
        op = event.op
        if not isinstance(op, Act):
            raise TypeError("recover requires an Act event")
        digest = op.digest
        dest_hash = _file_sha256(dest) if dest.is_file() else None
        if dest_hash == digest:
            # dest may itself be named .{ulid}.part; never unlink committed bytes
            if _resolved(staged) != _resolved(dest):
                staged.unlink(missing_ok=True)
            return
        if staged.is_file() and _file_sha256(staged) == digest:
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, dest)

    def _iter_part_files(self) -> list[Path]:
        if self.root is None or not self.root.exists():
            return []
        found: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [
                name for name in dirnames if name.casefold() != _RESERVED_DIR
            ]
            for name in filenames:
                if _PART_NAME.match(name):
                    found.append(Path(dirpath) / name)
        return found


@contextmanager
def _nonblocking_lock(mu: Lock) -> Iterator[None]:
    if not mu.acquire(blocking=False):
        raise WorldLocked("world is locked")
    try:
        yield
    finally:
        mu.release()
