import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from spaceten import World, WorldExists, WorldLocked
from spaceten.errors import DirtySpace, TruncatedLog
from spaceten.kernel.energy import Energy
from spaceten.kernel.event import Act, Event, Finish, Observe, Plan
from spaceten.kernel.invariant import invariant_issues
from spaceten.kernel.number import Id
from spaceten.kernel.time import FrozenClock
from spaceten.store.jsonl import JsonlStore


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _act(address: str, data: bytes) -> Act:
    return Act(
        tool="write_file",
        address=address,
        digest=_sha(data),
        size_bytes=len(data),
    )


def test_init_creates_spaceten_and_genesis(tmp_path: Path) -> None:
    instant = datetime(2026, 8, 15, 17, 0, 0, tzinfo=UTC)
    world = World.init(tmp_path, energy_cap=500, clock=FrozenClock(instant))
    meta = tmp_path / ".spaceten"
    assert (meta / "world.json").is_file()
    assert (meta / "events.jsonl").is_file()
    assert (meta / "energy.json").is_file()
    assert (meta / "cells.json").is_file()
    header = json.loads((meta / "world.json").read_text(encoding="utf-8"))
    assert header["schema"] == 1
    assert header["energy_cap"] == 500
    assert header["created_at"] == "2026-08-15T17:00:00Z"
    assert "+00:00" not in header["created_at"]
    lines = (meta / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["schema"] == 1
    assert event["op"]["op"] == "init"
    assert event["op"]["energy_cap_mj"] == 500
    assert event["wall"].endswith("Z")
    assert "+00:00" not in event["wall"]
    assert world.head is not None
    assert world.head.seq == 1
    with pytest.raises(WorldExists):
        World.init(tmp_path)


def test_persist_across_world_load(tmp_path: Path) -> None:
    (tmp_path / "IN.txt").write_bytes(b"hello")
    world = World.init(tmp_path, energy_cap=100)
    world.propose(
        "human",
        Observe(address="IN.txt", size_bytes=0, content_hash=None),
    )
    data = b"artifact"
    world.propose("human", _act("nested/OUT.md", data), data=data)
    world.propose("agent:null", Plan(provider="null", summary="x"), spend=Energy(5))
    world.propose("human", Finish(summary="done", artifact="nested/OUT.md"))
    assert (tmp_path / "nested" / "OUT.md").read_bytes() == data

    loaded = World.load(tmp_path)
    assert loaded.header.id == world.header.id
    assert loaded.account == world.account
    assert loaded.account.conserved()
    assert loaded.head is not None
    assert world.head is not None
    assert loaded.head.id == world.head.id
    assert (tmp_path / "nested" / "OUT.md").read_bytes() == data
    report = loaded.check()
    assert report.ok
    assert report.events == 5
    issues = invariant_issues(
        loaded._events, loaded.account, in_jail=loaded._address_in_jail
    )
    assert issues == []
    assert loaded.check(rebuild=True).ok


def test_invariants_hold_after_reload(tmp_path: Path) -> None:
    world = World.init(tmp_path, energy_cap=80)
    data = b"ok"
    world.propose("human", _act("OUT.md", data), data=data)
    loaded = World.load(tmp_path)
    report = loaded.check()
    assert report.ok
    codes = {issue.code for issue in report.issues}
    assert not {"I1", "I2", "I3", "I4", "I5", "I6", "I7"} & codes
    view = loaded.account
    assert view.cap.mj == view.spent.mj + view.remaining.mj


def test_flock_raises_world_locked(tmp_path: Path) -> None:
    world = World.init(tmp_path, energy_cap=20)
    lock_path = tmp_path / ".spaceten" / "lock"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import fcntl, sys, time\n"
                "handle = open(sys.argv[1], 'a+b')\n"
                "fcntl.flock(handle.fileno(), fcntl.LOCK_EX)\n"
                "print('ready', flush=True)\n"
                "time.sleep(30)\n"
            ),
            str(lock_path),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "ready"
        with pytest.raises(WorldLocked):
            World.load(tmp_path)
        with pytest.raises(WorldLocked):
            world.propose("human", Finish(summary="blocked"))
    finally:
        proc.kill()
        proc.wait()
    again = World.load(tmp_path)
    assert again.header.id == world.header.id


def test_staged_write_and_recover(tmp_path: Path) -> None:
    world = World.init(tmp_path, energy_cap=20)
    store = JsonlStore(tmp_path)
    data = b"recovered"
    dest = tmp_path / "OUT.md"
    event = Event(
        id="01ARZ3NDEKTSV4RRFFQ69G5FAW",
        seq=2,
        wall=datetime(2026, 8, 15, 17, 0, 0, tzinfo=UTC),
        parent=world.head.id if world.head else None,
        actor="human",
        energy_delta_mj=-2,
        energy_reason="io",
        op=_act("OUT.md", data),
    )
    staged = store.stage_write(Id(event.id), dest, data)
    assert staged == tmp_path / ".spaceten" / "tmp" / f"{event.id}.part"
    assert staged.is_file()
    store.append(event)
    assert not dest.exists()

    store.recover_writes(store.load_events())
    assert dest.read_bytes() == data
    assert not staged.exists()

    orphan = tmp_path / ".spaceten" / "tmp" / "01ARZ3NDEKTSV4RRFFQ69G5FAV.part"
    orphan.write_bytes(b"orphan")
    store.recover_writes(store.load_events())
    assert not orphan.exists()
    assert dest.read_bytes() == data


def test_recover_does_not_delete_ulid_part_dest(tmp_path: Path) -> None:
    world = World.init(tmp_path, energy_cap=20)
    name = ".01ARZ3NDEKTSV4RRFFQ69G5FAV.part"
    data = b"keep-me"
    world.propose("human", _act(name, data), data=data)
    dest = tmp_path / name
    assert dest.read_bytes() == data
    JsonlStore(tmp_path).recover_writes(JsonlStore(tmp_path).load_events())
    assert dest.read_bytes() == data
    loaded = World.load(tmp_path)
    assert dest.read_bytes() == data
    assert loaded.check().ok


def test_torn_last_line_refuses_load(tmp_path: Path) -> None:
    World.init(tmp_path, energy_cap=20)
    events_path = tmp_path / ".spaceten" / "events.jsonl"
    events_path.write_bytes(events_path.read_bytes() + b'{"schema": 1, "id": "partial')
    with pytest.raises(TruncatedLog, match="torn last line"):
        World.load(tmp_path)

    store = JsonlStore(tmp_path, skip_torn=True)
    skipped = store.load_events()
    assert len(skipped) == 1
    assert events_path.read_bytes().endswith(b"partial")

    loaded = World.load(tmp_path, truncate_partial=True)
    assert loaded.head is not None
    assert loaded.head.seq == 1
    assert b"partial" not in events_path.read_bytes()

    with pytest.raises(ValueError, match="clean"):
        World.load(tmp_path, truncate_partial=True)


def test_dirty_space_after_mutating_dest(tmp_path: Path) -> None:
    world = World.init(tmp_path, energy_cap=20)
    data = b"clean"
    world.propose("human", _act("OUT.md", data), data=data)
    (tmp_path / "OUT.md").write_bytes(b"tampered")
    report = world.check(rebuild=True)
    assert not report.ok
    assert any(issue.code == "dirty_space" for issue in report.issues)
    with pytest.raises(DirtySpace, match="dest mismatch"):
        World.load(tmp_path)
    assert (tmp_path / "OUT.md").read_bytes() == b"tampered"
