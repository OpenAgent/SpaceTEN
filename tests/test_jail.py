from pathlib import Path

import pytest

from spaceten.kernel.space import Address, CellNotFound, OutsideSpace, Workspace


def _workspace(tmp_path: Path) -> tuple[Path, Workspace]:
    root = tmp_path / "space"
    root.mkdir()
    return root, Workspace(root)


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/abs",
        "/etc/passwd",
        "C:\\foo",
        "C:/foo",
        "C:foo",
        "\\\\server\\share",
        "a\x00b",
        "foo\x00",
        "..",
        "../x",
        "../../etc/passwd",
        "a/..",
        "a/../b",
        "a/../../outside",
        "foo/../../etc/passwd",
        ".spaceten",
        ".spaceten/events.jsonl",
        ".spaceten/tmp/x",
        "./.spaceten",
        "./.spaceten/config.toml",
        ".SPACETEN",
        ".SpaceTen",
        ".SPACETEN/events.jsonl",
        "./.SPACETEN/events.jsonl",
    ],
)
def test_illegal_address_rejected(path: str) -> None:
    with pytest.raises(OutsideSpace):
        Address(path)


def test_dotdot_inside_jail_still_rejected(tmp_path: Path) -> None:
    root, ws = _workspace(tmp_path)
    (root / "bar").write_bytes(b"inside")
    (root / "foo").mkdir()
    # Resolves inside the root lexically, but ".." is illegal before join.
    with pytest.raises(OutsideSpace):
        Address("foo/../bar")
    with pytest.raises(OutsideSpace):
        ws.resolve(Address("foo/../bar"))
    assert (root / "bar").read_bytes() == b"inside"


def test_absolute_address_never_joins_root(tmp_path: Path) -> None:
    root, ws = _workspace(tmp_path)
    (tmp_path / "outside.txt").write_bytes(b"secret")
    with pytest.raises(OutsideSpace):
        ws.read(Address(str((tmp_path / "outside.txt").resolve())))


def test_symlink_file_outside_root(tmp_path: Path) -> None:
    root, ws = _workspace(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"secret")
    (root / "link").symlink_to(secret)
    with pytest.raises(OutsideSpace):
        ws.resolve(Address("link"))
    with pytest.raises(OutsideSpace):
        ws.read(Address("link"))
    with pytest.raises(OutsideSpace):
        ws.observe(Address("link"))


def test_relative_symlink_escape(tmp_path: Path) -> None:
    root, ws = _workspace(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"secret")
    (root / "link").symlink_to(Path("..") / "secret.txt")
    with pytest.raises(OutsideSpace):
        ws.read(Address("link"))


def test_symlink_dir_outside_then_child(tmp_path: Path) -> None:
    root, ws = _workspace(tmp_path)
    (tmp_path / "outside.txt").write_bytes(b"secret")
    (root / "link").symlink_to(tmp_path)
    with pytest.raises(OutsideSpace):
        ws.read(Address("link/outside.txt"))
    with pytest.raises(OutsideSpace):
        ws.list_dir(Address("link"))


def test_write_through_symlink_dir_escape(tmp_path: Path) -> None:
    root, ws = _workspace(tmp_path)
    (root / "link").symlink_to(tmp_path)
    with pytest.raises(OutsideSpace):
        ws.write(Address("link/pwned.txt"), b"no")
    assert not (tmp_path / "pwned.txt").exists()


def test_symlink_hop_outside_and_back(tmp_path: Path) -> None:
    root, ws = _workspace(tmp_path)
    (root / "inside.txt").write_bytes(b"in")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "back").symlink_to(root / "inside.txt")
    (root / "link").symlink_to(outside / "back")
    with pytest.raises(OutsideSpace):
        ws.read(Address("link"))
    with pytest.raises(OutsideSpace):
        ws.observe(Address("link"))


def test_symlink_staying_inside_is_allowed(tmp_path: Path) -> None:
    root, ws = _workspace(tmp_path)
    (root / "real.txt").write_bytes(b"hello")
    (root / "alias").symlink_to(root / "real.txt")
    (root / "rel").symlink_to("real.txt")
    assert ws.read(Address("alias")) == b"hello"
    assert ws.read(Address("rel")) == b"hello"
    cell = ws.observe(Address("alias"))
    assert cell.kind == "file"
    assert cell.size_bytes == 5

    dest = root / "dest"
    dest.mkdir()
    (dest / "file.txt").write_bytes(b"nested")
    (root / "destlink").symlink_to(dest)
    assert ws.read(Address("destlink/file.txt")) == b"nested"
    listing = ws.list_dir(Address("destlink"))
    assert [cell.address.path for cell in listing] == ["destlink/file.txt"]


def test_spaceten_dir_is_kernel_owned(tmp_path: Path) -> None:
    root, ws = _workspace(tmp_path)
    kernel = root / ".spaceten"
    kernel.mkdir()
    (kernel / "events.jsonl").write_bytes(b"log")
    (root / "IN.txt").write_bytes(b"in")

    with pytest.raises(OutsideSpace):
        Address(".spaceten")
    with pytest.raises(OutsideSpace):
        Address(".spaceten/events.jsonl")
    with pytest.raises(OutsideSpace):
        ws.write(Address(".spaceten/events.jsonl"), b"tamper")
    with pytest.raises(OutsideSpace):
        ws.read(Address(".spaceten/events.jsonl"))
    with pytest.raises(OutsideSpace):
        ws.observe(Address(".spaceten/events.jsonl"))
    with pytest.raises(OutsideSpace):
        ws.list_dir(Address(".spaceten"))
    with pytest.raises(OutsideSpace):
        ws.write(Address(".spaceten/new.txt"), b"no")

    assert (kernel / "events.jsonl").read_bytes() == b"log"
    assert not (kernel / "new.txt").exists()

    names = [cell.address.path for cell in ws.list_dir(Address("."))]
    assert "IN.txt" in names
    assert all(not name.startswith(".spaceten") for name in names)


def test_rejected_write_does_not_touch_disk(tmp_path: Path) -> None:
    root, ws = _workspace(tmp_path)
    before = {p.relative_to(root) for p in root.rglob("*")}
    with pytest.raises(OutsideSpace):
        Address("../escaped.txt")
    with pytest.raises(OutsideSpace):
        Address(".spaceten/x")
    after = {p.relative_to(root) for p in root.rglob("*")}
    assert after == before
    assert not (tmp_path / "escaped.txt").exists()


def test_list_dir_omits_symlink_escape_children(tmp_path: Path) -> None:
    root, ws = _workspace(tmp_path)
    (root / "ok.txt").write_bytes(b"ok")
    (root / "escape").symlink_to(tmp_path / "secret.txt")
    (tmp_path / "secret.txt").write_bytes(b"secret")
    names = [cell.address.path for cell in ws.list_dir(Address("."))]
    assert names == ["ok.txt"]


def test_nested_legal_paths_stay_inside(tmp_path: Path) -> None:
    root, ws = _workspace(tmp_path)
    cell = ws.write(Address("docs/notes/readme.md"), b"hi")
    assert (root / "docs" / "notes" / "readme.md").read_bytes() == b"hi"
    assert ws.observe(Address("docs")).kind == "dir"
    assert ws.read(Address("docs/notes/readme.md")) == b"hi"
    assert cell.address.parts() == ("docs", "notes", "readme.md")


def test_dot_segment_is_not_parent_escape(tmp_path: Path) -> None:
    root, ws = _workspace(tmp_path)
    (root / "foo").mkdir()
    (root / "foo" / "bar.txt").write_bytes(b"x")
    assert Address("foo/./bar.txt").parts() == ("foo", "bar.txt")
    assert ws.read(Address("foo/./bar.txt")) == b"x"


def test_missing_path_is_not_outside_space(tmp_path: Path) -> None:
    _, ws = _workspace(tmp_path)
    with pytest.raises(CellNotFound):
        ws.observe(Address("absent.txt"))
    with pytest.raises(CellNotFound):
        ws.read(Address("absent.txt"))
    with pytest.raises(CellNotFound):
        ws.list_dir(Address("absent"))


def test_symlink_to_spaceten_is_outside(tmp_path: Path) -> None:
    root, ws = _workspace(tmp_path)
    kernel = root / ".spaceten"
    kernel.mkdir()
    (kernel / "events.jsonl").write_bytes(b"log")
    (root / "sneaky").symlink_to(".spaceten")

    with pytest.raises(OutsideSpace):
        ws.resolve(Address("sneaky"))
    with pytest.raises(OutsideSpace):
        ws.read(Address("sneaky/events.jsonl"))
    with pytest.raises(OutsideSpace):
        ws.observe(Address("sneaky/events.jsonl"))
    with pytest.raises(OutsideSpace):
        ws.list_dir(Address("sneaky"))
    with pytest.raises(OutsideSpace):
        ws.write(Address("sneaky/pwned.txt"), b"tamper")

    assert (kernel / "events.jsonl").read_bytes() == b"log"
    assert not (kernel / "pwned.txt").exists()
    assert "sneaky" not in [cell.address.path for cell in ws.list_dir(Address("."))]


def test_spaceten_case_variants_do_not_touch_kernel(tmp_path: Path) -> None:
    root, _ws = _workspace(tmp_path)
    kernel = root / ".spaceten"
    kernel.mkdir()
    (kernel / "events.jsonl").write_bytes(b"log")
    before = sorted(p.name for p in kernel.iterdir())

    for path in (
        ".SPACETEN",
        ".SpaceTen",
        ".SPACETEN/hack.txt",
        ".SpaceTen/hack.txt",
        "./.SPACETEN/events.jsonl",
    ):
        with pytest.raises(OutsideSpace):
            Address(path)

    assert (kernel / "events.jsonl").read_bytes() == b"log"
    assert sorted(p.name for p in kernel.iterdir()) == before
    assert not (kernel / "hack.txt").exists()


def test_in_jail_symlink_via_unresolved_root_prefix(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / "real.txt").write_bytes(b"hello")
    (real / "rel").symlink_to("real.txt")
    prefix = tmp_path / "via"
    prefix.symlink_to(real)
    (real / "abs").symlink_to(prefix / "real.txt")
    ws = Workspace(prefix)
    assert ws.read(Address("real.txt")) == b"hello"
    assert ws.read(Address("rel")) == b"hello"
    assert ws.read(Address("abs")) == b"hello"


def test_workspace_via_var_symlink_prefix(tmp_path: Path) -> None:
    resolved = tmp_path.resolve()
    private_var = Path("/private/var")
    if not Path("/var").is_symlink() or private_var.resolve() not in (
        resolved,
        *resolved.parents,
    ):
        pytest.skip("/var is not a symlink prefix of tmp_path")
    via = Path("/var") / resolved.relative_to(private_var.resolve())
    root = via / "space"
    root.mkdir()
    (root / "real.txt").write_bytes(b"hello")
    (root / "rel").symlink_to("real.txt")
    (root / "abs").symlink_to(root / "real.txt")
    ws = Workspace(root)
    assert ws.read(Address("real.txt")) == b"hello"
    assert ws.read(Address("rel")) == b"hello"
    assert ws.read(Address("abs")) == b"hello"
