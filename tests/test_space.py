import hashlib
from datetime import UTC, timedelta
from pathlib import Path

import pytest

from spaceten.kernel.space import (
    Address,
    CellNotFound,
    OutsideSpace,
    Workspace,
    WriteTooLarge,
)


def test_address_dot_is_root_and_empty_parts() -> None:
    addr = Address(".")
    assert addr.path == "."
    assert addr.parts() == ()


def test_address_file_and_nested_parts() -> None:
    assert Address("IN.txt").parts() == ("IN.txt",)
    assert Address("a/b/c.md").parts() == ("a", "b", "c.md")


def test_address_is_frozen() -> None:
    addr = Address("IN.txt")
    with pytest.raises(AttributeError):
        addr.path = "OUT.txt"  # type: ignore[misc]


def test_cell_is_frozen(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    cell = ws.write(Address("f.txt"), b"x")
    with pytest.raises(AttributeError):
        cell.kind = "dir"  # type: ignore[misc]


def test_resolve_dot_is_root(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    assert ws.resolve(Address(".")) == tmp_path.resolve()


def test_resolve_relative_file(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    assert ws.resolve(Address("IN.txt")) == (tmp_path / "IN.txt").resolve()
    assert ws.resolve(Address("a/b/c.md")) == (tmp_path / "a" / "b" / "c.md").resolve()


def test_observe_file_returns_cell(tmp_path: Path) -> None:
    data = b"hello"
    (tmp_path / "IN.txt").write_bytes(data)
    ws = Workspace(tmp_path)
    cell = ws.observe(Address("IN.txt"))
    assert cell.address == Address("IN.txt")
    assert cell.kind == "file"
    assert cell.content_hash == hashlib.sha256(data).hexdigest()
    assert cell.size_bytes == len(data)
    assert cell.mtime_wall is not None
    assert cell.mtime_wall.tzinfo is not None
    assert cell.mtime_wall.utcoffset() == timedelta(0)


def test_observe_dir_has_no_hash(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    ws = Workspace(tmp_path)
    cell = ws.observe(Address("sub"))
    assert cell.kind == "dir"
    assert cell.content_hash is None
    assert cell.size_bytes == 0
    assert cell.mtime_wall is not None
    assert cell.mtime_wall.tzinfo == UTC


def test_observe_root(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    cell = ws.observe(Address("."))
    assert cell.kind == "dir"
    assert cell.content_hash is None
    assert cell.size_bytes == 0
    assert cell.address.path == "."


def test_observe_missing_raises(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    with pytest.raises(CellNotFound):
        ws.observe(Address("missing.txt"))


def test_read_returns_bytes(tmp_path: Path) -> None:
    (tmp_path / "IN.txt").write_bytes(b"payload\x00\xff")
    ws = Workspace(tmp_path)
    assert ws.read(Address("IN.txt")) == b"payload\x00\xff"


def test_read_missing_raises(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    with pytest.raises(CellNotFound):
        ws.read(Address("nope.bin"))


def test_read_directory_raises(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    ws = Workspace(tmp_path)
    with pytest.raises(IsADirectoryError):
        ws.read(Address("sub"))


def test_list_dir_returns_child_cells(tmp_path: Path) -> None:
    (tmp_path / "IN.txt").write_bytes(b"in")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.md").write_bytes(b"a")
    ws = Workspace(tmp_path)
    listing = ws.list_dir(Address("."))
    paths = [cell.address.path for cell in listing]
    assert paths == ["IN.txt", "sub"]
    by_path = {cell.address.path: cell for cell in listing}
    assert by_path["IN.txt"].kind == "file"
    assert by_path["IN.txt"].content_hash == hashlib.sha256(b"in").hexdigest()
    assert by_path["sub"].kind == "dir"
    assert by_path["sub"].content_hash is None
    nested = ws.list_dir(Address("sub"))
    assert [cell.address.path for cell in nested] == ["sub/a.md"]
    assert nested[0].kind == "file"
    assert nested[0].size_bytes == 1


def test_list_dir_empty(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    ws = Workspace(tmp_path)
    assert ws.list_dir(Address("empty")) == []


def test_list_dir_missing_raises(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    with pytest.raises(CellNotFound):
        ws.list_dir(Address("gone"))


def test_list_dir_file_raises(tmp_path: Path) -> None:
    (tmp_path / "IN.txt").write_bytes(b"x")
    ws = Workspace(tmp_path)
    with pytest.raises(NotADirectoryError):
        ws.list_dir(Address("IN.txt"))


def test_write_creates_parents_and_returns_cell(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    data = b"nested"
    cell = ws.write(Address("a/b/c.md"), data)
    assert (tmp_path / "a" / "b" / "c.md").read_bytes() == data
    assert cell.address == Address("a/b/c.md")
    assert cell.kind == "file"
    assert cell.content_hash == hashlib.sha256(data).hexdigest()
    assert cell.size_bytes == len(data)
    assert ws.read(Address("a/b/c.md")) == data
    assert ws.observe(Address("a/b/c.md")).content_hash == cell.content_hash


def test_write_overwrites_existing(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    ws.write(Address("f.txt"), b"old")
    cell = ws.write(Address("f.txt"), b"new")
    assert ws.read(Address("f.txt")) == b"new"
    assert cell.content_hash == hashlib.sha256(b"new").hexdigest()


def test_write_empty_file(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    cell = ws.write(Address("empty.txt"), b"")
    assert cell.size_bytes == 0
    assert cell.content_hash == hashlib.sha256(b"").hexdigest()
    assert ws.read(Address("empty.txt")) == b""


def test_write_too_large_before_disk(tmp_path: Path) -> None:
    ws = Workspace(tmp_path, max_write_bytes=4)
    with pytest.raises(WriteTooLarge):
        ws.write(Address("nested/out.txt"), b"12345")
    assert not (tmp_path / "nested").exists()
    assert not (tmp_path / "nested" / "out.txt").exists()


def test_write_accepts_exact_max_bytes(tmp_path: Path) -> None:
    ws = Workspace(tmp_path, max_write_bytes=4)
    cell = ws.write(Address("ok.txt"), b"1234")
    assert cell.size_bytes == 4
    assert ws.read(Address("ok.txt")) == b"1234"


def test_max_write_bytes_default_and_property(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    assert ws.max_write_bytes == 1_048_576
    limited = Workspace(tmp_path, max_write_bytes=8)
    assert limited.max_write_bytes == 8


def test_space_error_types() -> None:
    for cls in (OutsideSpace, WriteTooLarge, CellNotFound):
        assert issubclass(cls, Exception)
        assert str(cls("x")) == "x"
