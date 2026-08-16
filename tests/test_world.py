import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

import spaceten
from spaceten import (
    AccountView,
    Address,
    Cell,
    CellNotFound,
    CheckIssue,
    CheckReport,
    Energy,
    EnergyExhausted,
    Event,
    Id,
    InvariantError,
    OutsideSpace,
    Receipt,
    StaleParent,
    World,
    WorldExists,
    WorldHeader,
    WorldLocked,
    WriteTooLarge,
)
from spaceten.kernel.event import Act, Failed, Finish, Init, Observe, Plan
from spaceten.kernel.space import Workspace
from spaceten.kernel.time import FrozenClock
from spaceten.store.memory import MemoryStore


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _act(address: str, data: bytes, tool: str = "write_file") -> Act:
    return Act(tool=tool, address=address, digest=_sha(data), size_bytes=len(data))


def _world(tmp_path: Path, *, energy_cap: int = 1_000, **kwargs: object) -> World:
    store = kwargs.pop("store", MemoryStore())
    return World.init(tmp_path, energy_cap=energy_cap, store=store, **kwargs)  # type: ignore[arg-type]


def test_public_exports() -> None:
    for name in (
        "World",
        "WorldHeader",
        "Receipt",
        "CheckReport",
        "CheckIssue",
        "Energy",
        "AccountView",
        "EnergyExhausted",
        "Address",
        "Cell",
        "OutsideSpace",
        "WriteTooLarge",
        "CellNotFound",
        "Event",
        "Id",
        "StaleParent",
        "InvariantError",
        "WorldExists",
        "WorldLocked",
    ):
        assert hasattr(spaceten, name)
    assert Address is spaceten.Address
    assert Cell is spaceten.Cell
    assert InvariantError is spaceten.InvariantError
    assert WorldLocked is spaceten.WorldLocked
    assert not hasattr(spaceten, "Provider")
    assert not hasattr(spaceten, "debit")


def test_init_genesis_header_and_account(tmp_path: Path) -> None:
    instant = datetime(2026, 8, 15, 17, 0, 0, tzinfo=UTC)
    world = _world(tmp_path, energy_cap=500, clock=FrozenClock(instant))
    assert world.header.schema == 1
    assert world.header.energy_cap == 500
    assert world.header.root_policy == "jail"
    assert world.header.energy_unit == "mj"
    assert world.header.created_at == instant
    assert world.head is not None
    assert isinstance(world.head.op, Init)
    assert world.head.op.energy_cap_mj == 500
    assert world.head.parent is None
    assert world.head.seq == 1
    assert world.head.energy_delta_mj == 0
    assert world.head.energy_reason == "none"
    assert world.account == AccountView(Energy(500), Energy(0), Energy(500))
    assert world.account.conserved()
    assert not hasattr(world, "debit")
    assert not hasattr(world.account, "debit")
    assert not (tmp_path / ".spaceten").exists()
    report = world.check()
    assert report.ok
    assert report.events == 1
    assert report.remaining == Energy(500)


def test_init_fail_if_spaceten_exists(tmp_path: Path) -> None:
    (tmp_path / ".spaceten").mkdir()
    with pytest.raises(WorldExists):
        World.init(tmp_path, store=MemoryStore())


def test_store_none_is_not_implemented(tmp_path: Path) -> None:
    with pytest.raises(NotImplementedError, match="JsonlStore"):
        World.init(tmp_path)
    with pytest.raises(NotImplementedError, match="JsonlStore"):
        World.load(tmp_path)


def test_propose_observe_list_dir_and_file(tmp_path: Path) -> None:
    (tmp_path / "IN.txt").write_bytes(b"hello")
    world = _world(tmp_path)
    listing = world.propose(
        "human",
        Observe(address=".", size_bytes=0, content_hash=None, listing=True),
    )
    assert listing.event.energy_delta_mj == -1
    assert listing.event.energy_reason == "io"
    assert listing.cell is not None
    assert listing.cell.kind == "dir"
    assert listing.bytes_read is None
    assert isinstance(listing.event.op, Observe)
    assert listing.event.op.listing

    observed = world.propose(
        "human",
        Observe(address="IN.txt", size_bytes=0, content_hash=None),
    )
    assert observed.bytes_read == b"hello"
    assert observed.cell is not None
    assert observed.cell.size_bytes == 5
    assert observed.cell.content_hash == _sha(b"hello")
    assert observed.event.energy_delta_mj == -1
    assert world.account.spent == Energy(2)


def test_propose_act_then_observe_reads_dest_bytes(tmp_path: Path) -> None:
    world = _world(tmp_path)
    data = b"artifact"
    receipt = world.propose("human", _act("nested/OUT.md", data), data=data)
    dest = tmp_path / "nested" / "OUT.md"
    assert dest.read_bytes() == data
    assert receipt.cell is not None
    assert receipt.cell.content_hash == _sha(data)
    assert receipt.event.energy_delta_mj == -2
    assert isinstance(receipt.event.op, Act)
    assert receipt.event.op.digest == _sha(data)

    observed = world.propose(
        "human",
        Observe(address="nested/OUT.md", size_bytes=0, content_hash=None),
    )
    assert observed.bytes_read == data
    assert world.account.conserved()


def test_propose_act_does_not_call_workspace_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Workspace.write must not be used by World.propose")

    monkeypatch.setattr(Workspace, "write", boom)
    world = _world(tmp_path)
    data = b"ok"
    world.propose("human", _act("OUT.md", data), data=data)
    assert (tmp_path / "OUT.md").read_bytes() == data


def test_propose_plan_finish_failed(tmp_path: Path) -> None:
    world = _world(tmp_path, energy_cap=50)
    plan = world.propose(
        "agent:null",
        Plan(provider="null", model="null", summary="write OUT.md"),
        spend=Energy(5),
    )
    assert plan.event.energy_delta_mj == -5
    assert plan.event.energy_reason == "plan"
    assert world.account.remaining == Energy(45)

    finished = world.propose("human", Finish(summary="done", artifact="OUT.md"))
    assert finished.event.energy_delta_mj == 0
    assert finished.event.energy_reason == "none"

    failed = world.propose("human", Failed(code="no_tool_calls", message="none"))
    assert failed.event.energy_delta_mj == 0
    assert world.account.spent == Energy(5)
    assert world.check().ok


def test_failed_overspend_debits_min_and_sets_attempted(tmp_path: Path) -> None:
    world = _world(tmp_path, energy_cap=3)
    receipt = world.propose(
        "human",
        Failed(code="energy_exhausted", message="too much"),
        spend=Energy(10),
    )
    assert receipt.event.energy_delta_mj == -3
    assert isinstance(receipt.event.op, Failed)
    assert receipt.event.op.attempted_mj == 10
    assert world.account.remaining == Energy(0)
    assert world.account.spent == Energy(3)
    assert world.account.conserved()
    assert world.check().ok


def test_energy_conservation_across_ops(tmp_path: Path) -> None:
    (tmp_path / "IN.txt").write_bytes(b"hello")
    world = _world(tmp_path, energy_cap=100)
    world.propose("human", Observe(address="IN.txt", size_bytes=0, content_hash=None))
    data = b"out"
    world.propose("human", _act("OUT.md", data), data=data)
    world.propose("agent:null", Plan(provider="null", summary="x"), spend=Energy(5))
    world.propose("human", Finish(summary="done"))
    view = world.account
    assert view.conserved()
    assert view.cap == Energy(100)
    assert view.spent == Energy(1 + 2 + 5)
    assert view.remaining == Energy(92)
    assert world.check().ok
    world.replay()
    assert world.account == view


def test_stale_parent_writes_nothing(tmp_path: Path) -> None:
    world = _world(tmp_path)
    genesis = world.head
    assert genesis is not None
    world.propose("human", Finish(summary="first"))
    before = world.head
    assert before is not None
    with pytest.raises(StaleParent):
        world.propose("human", Finish(summary="second"), parent=Id(genesis.id))
    assert world.head is not None
    assert world.head.id == before.id
    assert isinstance(world.head.op, Finish)
    assert world.head.op.summary == "first"


def test_parent_none_uses_head(tmp_path: Path) -> None:
    world = _world(tmp_path)
    genesis = world.head
    assert genesis is not None
    receipt = world.propose("human", Finish(summary="next"))
    assert receipt.event.parent == genesis.id
    assert receipt.event.seq == 2


def test_explicit_head_parent_is_accepted(tmp_path: Path) -> None:
    world = _world(tmp_path)
    genesis = world.head
    assert genesis is not None
    receipt = world.propose("human", Finish(summary="ok"), parent=Id(genesis.id))
    assert receipt.event.parent == genesis.id


def test_jail_observe_and_act_write_nothing(tmp_path: Path) -> None:
    world = _world(tmp_path)
    genesis = world.head
    with pytest.raises(OutsideSpace):
        world.propose(
            "human",
            Observe(address=".spaceten", size_bytes=0, content_hash=None),
        )
    with pytest.raises(OutsideSpace):
        world.propose("human", Observe(address="..", size_bytes=0, content_hash=None))
    with pytest.raises(OutsideSpace):
        world.propose("human", _act(".spaceten/x", b"no"), data=b"no")
    with pytest.raises(OutsideSpace):
        world.propose("human", _act("../escaped.txt", b"no"), data=b"no")
    assert world.head == genesis
    assert not (tmp_path / ".spaceten").exists()
    assert not (tmp_path / "escaped.txt").exists()
    assert list(tmp_path.iterdir()) == []


def test_propose_act_rejects_directory_dest(tmp_path: Path) -> None:
    (tmp_path / "subdir").mkdir()
    world = _world(tmp_path)
    genesis = world.head
    spent = world.account.spent
    parent = tmp_path.resolve().parent
    before_outside = {p.name for p in parent.iterdir() if p.name.endswith(".part")}
    with pytest.raises(IsADirectoryError):
        world.propose("human", _act(".", b"no"), data=b"no")
    with pytest.raises(IsADirectoryError):
        world.propose("human", _act("subdir", b"no"), data=b"no")
    assert world.head == genesis
    assert world.account.spent == spent
    assert world.account.conserved()
    assert not list(tmp_path.glob("**/.*.part"))
    after_outside = {p.name for p in parent.iterdir() if p.name.endswith(".part")}
    assert after_outside == before_outside
    assert (tmp_path / "subdir").is_dir()


def test_write_too_large_writes_nothing(tmp_path: Path) -> None:
    world = _world(tmp_path, max_write_bytes=4)
    genesis = world.head
    with pytest.raises(WriteTooLarge):
        world.propose("human", _act("nested/out.txt", b"12345"), data=b"12345")
    assert world.head == genesis
    assert not (tmp_path / "nested").exists()
    assert not (tmp_path / "nested" / "out.txt").exists()
    assert not list(tmp_path.glob("**/*.part"))


def test_energy_exhausted_before_act_side_effects(tmp_path: Path) -> None:
    world = _world(tmp_path, energy_cap=1)
    genesis = world.head
    data = b"x"
    with pytest.raises(EnergyExhausted) as excinfo:
        world.propose("human", _act("OUT.md", data), data=data)
    assert excinfo.value.remaining == Energy(1)
    assert excinfo.value.requested == Energy(2)
    assert world.head == genesis
    assert not (tmp_path / "OUT.md").exists()
    assert not list(tmp_path.glob("**/*.part"))


def test_observe_missing_is_cell_not_found(tmp_path: Path) -> None:
    world = _world(tmp_path)
    genesis = world.head
    with pytest.raises(CellNotFound):
        world.propose(
            "human",
            Observe(address="gone.txt", size_bytes=0, content_hash=None),
        )
    assert world.head == genesis


def test_observe_act_plan_reject_wrong_spend(tmp_path: Path) -> None:
    world = _world(tmp_path)
    with pytest.raises(ValueError, match="Observe spend"):
        world.propose(
            "human",
            Observe(address=".", size_bytes=0, content_hash=None, listing=True),
            spend=Energy(1),
        )
    with pytest.raises(ValueError, match="Act spend"):
        world.propose("human", _act("OUT.md", b"x"), spend=Energy(1), data=b"x")
    with pytest.raises(ValueError, match="Plan spend"):
        world.propose("human", Plan(provider="null", summary="x"))
    with pytest.raises(ValueError, match="Plan spend"):
        world.propose("human", Plan(provider="null", summary="x"), spend=Energy(0))
    with pytest.raises(ValueError, match="Finish spend"):
        world.propose("human", Finish(summary="x"), spend=Energy(1))
    with pytest.raises(ValueError, match="Init"):
        world.propose("human", Init(energy_cap_mj=1))


def test_plan_overspend_is_precommit(tmp_path: Path) -> None:
    world = _world(tmp_path, energy_cap=4)
    genesis = world.head
    with pytest.raises(EnergyExhausted):
        world.propose("human", Plan(provider="null", summary="x"), spend=Energy(5))
    assert world.head == genesis
    assert world.account.spent == Energy(0)


def test_load_roundtrip(tmp_path: Path) -> None:
    store = MemoryStore()
    world = World.init(tmp_path, energy_cap=40, store=store)
    data = b"keep"
    world.propose("human", _act("OUT.md", data), data=data)
    loaded = World.load(tmp_path, store=store)
    assert loaded.header.id == world.header.id
    assert loaded.account == world.account
    assert loaded.head is not None
    assert world.head is not None
    assert loaded.head.id == world.head.id
    assert (tmp_path / "OUT.md").read_bytes() == data
    assert loaded.check().ok


def test_check_rebuild_dirty_space(tmp_path: Path) -> None:
    world = _world(tmp_path)
    data = b"clean"
    world.propose("human", _act("OUT.md", data), data=data)
    assert world.check(rebuild=True).ok
    (tmp_path / "OUT.md").write_bytes(b"tampered")
    report = world.check(rebuild=True)
    assert not report.ok
    assert any(issue.code == "dirty_space" for issue in report.issues)


def test_recover_orphan_part_and_finish_replace(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    world = World.init(tmp_path, energy_cap=20, store=store)
    orphan = tmp_path / ".01ARZ3NDEKTSV4RRFFQ69G5FAV.part"
    orphan.write_bytes(b"orphan")
    store.recover_writes(store.load_events())
    assert not orphan.exists()

    data = b"recovered"
    act = _act("OUT.md", data)
    event = Event(
        id="01ARZ3NDEKTSV4RRFFQ69G5FAW",
        seq=2,
        wall=datetime(2026, 8, 15, 17, 0, 0, tzinfo=UTC),
        parent=world.head.id if world.head else None,
        actor="human",
        energy_delta_mj=-2,
        energy_reason="io",
        op=act,
    )
    staged = tmp_path / f".{event.id}.part"
    staged.write_bytes(data)
    store.append(event)
    assert not (tmp_path / "OUT.md").exists()
    store.recover_writes(store.load_events())
    assert (tmp_path / "OUT.md").read_bytes() == data
    assert not staged.exists()


def test_recover_does_not_delete_ulid_part_dest(tmp_path: Path) -> None:
    store = MemoryStore()
    world = World.init(tmp_path, energy_cap=20, store=store)
    name = ".01ARZ3NDEKTSV4RRFFQ69G5FAV.part"
    data = b"keep-me"
    world.propose("human", _act(name, data), data=data)
    dest = tmp_path / name
    assert dest.read_bytes() == data
    store.recover_writes(store.load_events())
    assert dest.read_bytes() == data
    loaded = World.load(tmp_path, store=store)
    assert dest.read_bytes() == data
    assert loaded.check().ok


def test_receipt_types(tmp_path: Path) -> None:
    world = _world(tmp_path)
    receipt = world.propose("human", Finish(summary="n"))
    assert isinstance(receipt, Receipt)
    assert isinstance(receipt.event, Event)
    assert isinstance(receipt.remaining, Energy)
    assert isinstance(world.header, WorldHeader)
    assert isinstance(world.check(), CheckReport)
    assert isinstance(CheckIssue("I1", "x"), CheckIssue)
