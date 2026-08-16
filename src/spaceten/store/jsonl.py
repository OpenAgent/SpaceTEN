import hashlib
import json
import os
from contextlib import AbstractContextManager
from pathlib import Path

from spaceten.errors import DirtySpace, TruncatedLog
from spaceten.kernel.energy import AccountView
from spaceten.kernel.event import Act, Event
from spaceten.kernel.number import Id
from spaceten.kernel.space import Address, Cell, OutsideSpace, Workspace
from spaceten.store.lock import exclusive_lock
from spaceten.store.paths import StorePaths
from spaceten.store.protocol import WorldHeader
from spaceten.store.state import (
    atomic_write,
    dump_cells,
    dump_energy,
    dump_header,
    load_cells,
    load_energy,
    load_header,
)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


class JsonlStore:
    """Append-only events.jsonl under root/.spaceten/; flock + staged Act writes."""

    def __init__(
        self,
        root: Path,
        *,
        truncate_partial: bool = False,
        skip_torn: bool = False,
    ) -> None:
        self.root = Path(root).resolve()
        self.paths = StorePaths.for_root(self.root)
        self.truncate_partial = truncate_partial
        self.skip_torn = skip_torn

    def _ensure_layout(self) -> None:
        self.paths.meta.mkdir(parents=True, exist_ok=True)
        self.paths.tmp.mkdir(parents=True, exist_ok=True)

    def append(self, event: Event) -> None:
        self._ensure_layout()
        line = event.model_dump_json().encode("utf-8") + b"\n"
        with open(self.paths.events, "ab") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    def load_events(self) -> list[Event]:
        path = self.paths.events
        if not path.exists():
            if self.truncate_partial:
                raise ValueError("truncate_partial refused on a clean events.jsonl")
            return []
        raw = path.read_bytes()
        if not raw:
            if self.truncate_partial:
                raise ValueError("truncate_partial refused on a clean events.jsonl")
            return []
        # Split only on \n so U+2028/U+2029/NEL inside JSON strings stay intact.
        chunks = raw.split(b"\n")
        if chunks and chunks[-1] == b"":
            chunks = chunks[:-1]
        events: list[Event] = []
        torn = False
        for index, chunk in enumerate(chunks):
            if not chunk.strip():
                continue
            is_last = index == len(chunks) - 1
            try:
                line = chunk.decode("utf-8")
            except UnicodeDecodeError as exc:
                if is_last:
                    torn = True
                    break
                raise ValueError(f"corrupt events.jsonl line {index + 1}") from exc
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                if is_last:
                    torn = True
                    break
                raise ValueError(f"corrupt events.jsonl line {index + 1}") from exc
            events.append(Event.model_validate(payload))
        if torn:
            if self.truncate_partial:
                self._rewrite_events(events)
                # one-shot: World.load reads the log again after recover_writes
                self.truncate_partial = False
                return events
            if self.skip_torn:
                return events
            raise TruncatedLog("torn last line in events.jsonl")
        if self.truncate_partial:
            raise ValueError("truncate_partial refused on a clean events.jsonl")
        return events

    def load_header(self) -> WorldHeader:
        path = self.paths.world
        if not path.exists():
            raise FileNotFoundError("world header is missing")
        return load_header(path.read_bytes())

    def save_header(self, header: WorldHeader) -> None:
        self._ensure_layout()
        atomic_write(self.paths.world, dump_header(header))

    def save_caches(self, account: AccountView, cells: dict[str, Cell]) -> None:
        self._ensure_layout()
        atomic_write(self.paths.energy, dump_energy(account))
        atomic_write(self.paths.cells, dump_cells(cells))

    def load_caches(self) -> tuple[AccountView, dict[str, Cell]] | None:
        if not self.paths.energy.exists() or not self.paths.cells.exists():
            return None
        return load_energy(self.paths.energy.read_bytes()), load_cells(
            self.paths.cells.read_bytes()
        )

    def lock(self) -> AbstractContextManager[None]:
        return exclusive_lock(self.paths.lock)

    def stage_write(self, event_id: Id, dest: Path, data: bytes) -> Path:
        # Stage under .spaceten/tmp, not dest.parent — dest may itself be named *.part.
        self._ensure_layout()
        staged = self.paths.staged(event_id)
        with open(staged, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        return staged

    def commit_write(self, staged: Path, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged, dest)

    def recover_writes(self, events: list[Event]) -> None:
        last_act: dict[str, Event] = {}
        for event in events:
            if isinstance(event.op, Act):
                last_act[event.op.address] = event
        seen: set[Path] = set()
        dirty: list[str] = []
        workspace = Workspace(self.root)
        for event in last_act.values():
            op = event.op
            if not isinstance(op, Act):
                continue
            staged = self.paths.staged(event.id)
            seen.add(_resolved(staged))
            try:
                dest = workspace.resolve(Address(op.address))
            except OutsideSpace:
                dirty.append(op.address)
                continue
            if not self._recover_act(event, dest, staged):
                dirty.append(op.address)
        if self.paths.tmp.exists():
            for staged in self.paths.tmp.iterdir():
                if staged.suffix != ".part" or not staged.is_file():
                    continue
                if _resolved(staged) in seen:
                    continue
                # only tmp/ orphans; never unlink a committed dest that looks like .part
                staged.unlink(missing_ok=True)
        if dirty:
            raise DirtySpace(
                "dest mismatch without staged recovery: " + ", ".join(dirty)
            )

    def _recover_act(self, event: Event, dest: Path, staged: Path) -> bool:
        op = event.op
        if not isinstance(op, Act):
            raise TypeError("recover requires an Act event")
        dest_hash = _file_sha256(dest) if dest.is_file() else None
        if dest_hash == op.digest:
            if staged.is_file() and _resolved(staged) != _resolved(dest):
                staged.unlink(missing_ok=True)
            return True
        if staged.is_file() and _file_sha256(staged) == op.digest:
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, dest)
            return True
        # dest mismatch and no matching staged file: do not invent or overwrite bytes
        return False

    def _rewrite_events(self, events: list[Event]) -> None:
        self._ensure_layout()
        tmp = self.paths.events.with_name(self.paths.events.name + ".tmp")
        with open(tmp, "wb") as handle:
            for event in events:
                handle.write(event.model_dump_json().encode("utf-8") + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.paths.events)
