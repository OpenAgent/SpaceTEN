import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal


class OutsideSpace(Exception):
    """Address resolved outside the workspace jail or names kernel-owned space."""


class WriteTooLarge(Exception):
    """write() refused because data exceeds max_write_bytes."""


class CellNotFound(Exception):
    """observe/read/list_dir of a missing path."""


def _windows_drive(path: str) -> bool:
    if len(path) >= 2 and path[0].isalpha() and path[1] == ":":
        return True
    return path.startswith("\\\\")


def _raw_parts(path: str) -> list[str]:
    if path == ".":
        return []
    return path.split("/")


def _reject_illegal_address(path: str) -> None:
    if path == ".":
        return
    if path == "" or "\x00" in path or path.startswith("/") or _windows_drive(path):
        raise OutsideSpace(path)
    raw = _raw_parts(path)
    # Reject ".." before any join so lexical escapes never reach Path.resolve.
    if any(part == ".." for part in raw):
        raise OutsideSpace(path)
    parts = tuple(part for part in raw if part and part != ".")
    if parts and parts[0] == ".spaceten":
        raise OutsideSpace(path)


def _symlink_first_hop(link: Path) -> Path:
    target = link.readlink()
    if not target.is_absolute():
        target = link.parent / target
    return Path(os.path.normpath(target))


@dataclass(frozen=True, slots=True)
class Address:
    path: str  # POSIX, relative, no leading '/'

    def __post_init__(self) -> None:
        _reject_illegal_address(self.path)

    def parts(self) -> tuple[str, ...]:
        if self.path == ".":
            return ()
        return tuple(part for part in self.path.split("/") if part and part != ".")


@dataclass(frozen=True, slots=True)
class Cell:
    address: Address
    kind: Literal["file", "dir"]
    content_hash: str | None  # sha256 hex of file bytes; None for dir
    size_bytes: int
    mtime_wall: datetime | None


class Workspace:
    def __init__(self, root: Path, *, max_write_bytes: int = 1_048_576) -> None:
        self._root = root
        self._max_write_bytes = max_write_bytes

    @property
    def max_write_bytes(self) -> int:
        return self._max_write_bytes

    def resolve(self, address: Address) -> Path:
        _reject_illegal_address(address.path)
        root = self._root.resolve()
        if address.path == ".":
            joined = root
        else:
            joined = (self._root / address.path).resolve()
        if joined != root and root not in joined.parents:
            raise OutsideSpace(address.path)
        self._reject_symlink_escape(address, root)
        return joined

    def observe(self, address: Address) -> Cell:
        path = self.resolve(address)
        if not path.exists():
            raise CellNotFound(address.path)
        return self._to_cell(address, path)

    def read(self, address: Address) -> bytes:
        path = self.resolve(address)
        if not path.exists():
            raise CellNotFound(address.path)
        if path.is_dir():
            raise IsADirectoryError(address.path)
        return path.read_bytes()

    def list_dir(self, address: Address) -> list[Cell]:
        path = self.resolve(address)
        if not path.exists():
            raise CellNotFound(address.path)
        if not path.is_dir():
            raise NotADirectoryError(address.path)
        prefix = "" if address.path == "." else address.path.rstrip("/") + "/"
        cells: list[Cell] = []
        for child in sorted(path.iterdir(), key=lambda p: p.name):
            child_rel = f"{prefix}{child.name}"
            try:
                child_addr = Address(child_rel)
                resolved = self.resolve(child_addr)
            except OutsideSpace:
                continue
            if not resolved.exists():
                continue
            cells.append(self._to_cell(child_addr, resolved))
        return cells

    def write(self, address: Address, data: bytes) -> Cell:
        if len(data) > self._max_write_bytes:
            raise WriteTooLarge(f"{len(data)} > {self._max_write_bytes}")
        path = self.resolve(address)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return self._to_cell(address, path)

    def _reject_symlink_escape(self, address: Address, root: Path) -> None:
        cursor = Path(self._root)
        for part in _raw_parts(address.path):
            if part in ("", "."):
                continue
            cursor = cursor / part
            if cursor.is_symlink():
                hop = _symlink_first_hop(cursor)
                if hop != root and root not in hop.parents:
                    raise OutsideSpace(address.path)

    def _to_cell(self, address: Address, path: Path) -> Cell:
        stat = path.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
        if path.is_dir():
            return Cell(
                address=address,
                kind="dir",
                content_hash=None,
                size_bytes=0,
                mtime_wall=mtime,
            )
        data = path.read_bytes()
        return Cell(
            address=address,
            kind="file",
            content_hash=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
            mtime_wall=mtime,
        )
