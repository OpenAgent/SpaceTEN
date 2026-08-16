from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from spaceten.kernel.event import Act, Event, Failed, Finish, Init, Observe, Plan, utc_z


def _event(**overrides: object) -> Event:
    payload: dict[str, object] = {
        "id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "seq": 1,
        "wall": datetime(2026, 8, 15, 17, 0, 0, tzinfo=UTC),
        "parent": None,
        "actor": "kernel",
        "energy_delta_mj": 0,
        "energy_reason": "none",
        "op": Init(energy_cap_mj=100),
    }
    payload.update(overrides)
    return Event.model_validate(payload)


def test_init_observe_plan_act_finish_failed_construct() -> None:
    assert Init(energy_cap_mj=100).op == "init"
    assert Observe(address=".", size_bytes=0, content_hash=None, listing=True).listing
    assert Plan(provider="null", summary="go").tokens_in == 0
    act = Act(tool="write_file", address="OUT.md", digest="ab", size_bytes=1)
    assert act.tool == "write_file"
    assert Finish(summary="done").artifact is None
    assert Failed(code="max_steps", message="stop").attempted_mj is None


def test_event_schema_is_one() -> None:
    event = _event()
    assert event.schema_version == 1
    dumped = event.model_dump(mode="json")
    assert dumped["schema"] == 1


def test_event_rejects_positive_energy_delta() -> None:
    with pytest.raises(ValidationError, match="energy_delta_mj"):
        _event(energy_delta_mj=1, energy_reason="io")


def test_event_zero_delta_requires_none_reason() -> None:
    with pytest.raises(ValidationError, match="energy_reason"):
        _event(energy_delta_mj=0, energy_reason="io")
    with pytest.raises(ValidationError, match="energy_reason"):
        _event(
            energy_delta_mj=-1,
            energy_reason="none",
            op=Observe(address="IN.txt", size_bytes=1, content_hash="aa"),
        )


def test_event_accepts_negative_io_and_plan_deltas() -> None:
    observe = _event(
        energy_delta_mj=-1,
        energy_reason="io",
        op=Observe(address="IN.txt", size_bytes=5, content_hash="aa"),
    )
    assert observe.energy_delta_mj == -1
    plan = _event(
        seq=2,
        parent=observe.id,
        energy_delta_mj=-5,
        energy_reason="plan",
        op=Plan(provider="null", summary="x"),
    )
    assert plan.energy_reason == "plan"


def test_json_wall_uses_utc_z() -> None:
    event = _event()
    dumped = event.model_dump(mode="json")
    assert dumped["wall"] == "2026-08-15T17:00:00Z"
    assert "+00:00" not in dumped["wall"]
    raw = event.model_dump_json()
    assert "2026-08-15T17:00:00Z" in raw
    assert "+00:00" not in raw


def test_json_wall_roundtrip() -> None:
    event = _event()
    restored = Event.model_validate_json(event.model_dump_json())
    assert restored.wall == event.wall
    assert restored.op == event.op
    assert restored.schema_version == 1


def test_op_discriminator_roundtrip() -> None:
    cases: list[Event] = [
        _event(),
        _event(
            energy_delta_mj=-1,
            energy_reason="io",
            op=Observe(address=".", size_bytes=0, content_hash=None, listing=True),
        ),
        _event(
            energy_delta_mj=-5,
            energy_reason="plan",
            op=Plan(provider="null", summary="p"),
        ),
        _event(
            energy_delta_mj=-2,
            energy_reason="io",
            op=Act(tool="write_file", address="OUT.md", digest="ab", size_bytes=1),
        ),
        _event(op=Finish(summary="done", artifact="OUT.md")),
        _event(op=Failed(code="no_tool_calls", message="none", attempted_mj=3)),
    ]
    for event in cases:
        restored = Event.model_validate_json(event.model_dump_json())
        assert restored.op.op == event.op.op
        assert type(restored.op) is type(event.op)


def test_utc_z_never_plus_offset() -> None:
    instant = datetime(2026, 8, 15, 17, 0, 0, tzinfo=UTC)
    assert utc_z(instant) == "2026-08-15T17:00:00Z"
    with_frac = datetime(2026, 8, 15, 17, 0, 0, 123456, tzinfo=UTC)
    assert utc_z(with_frac) == "2026-08-15T17:00:00.123456Z"
    assert "+00:00" not in utc_z(with_frac)


def test_init_rejects_negative_cap() -> None:
    with pytest.raises(ValidationError):
        Init(energy_cap_mj=-1)


def test_event_is_frozen() -> None:
    event = _event()
    with pytest.raises(ValidationError):
        event.seq = 2  # type: ignore[misc]
