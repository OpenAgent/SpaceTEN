from datetime import UTC, datetime
from types import SimpleNamespace

from spaceten.kernel.energy import AccountView, Energy
from spaceten.kernel.event import Act, Event, Failed, Finish, Init, Observe, Plan
from spaceten.kernel.invariant import invariant_issues, spend_from


def _event(**overrides: object) -> Event:
    payload: dict[str, object] = {
        "id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "seq": 1,
        "wall": datetime(2026, 8, 15, 17, 0, 0, tzinfo=UTC),
        "parent": None,
        "actor": "kernel",
        "energy_delta_mj": 0,
        "energy_reason": "none",
        "op": Init(energy_cap_mj=10),
    }
    payload.update(overrides)
    return Event.model_validate(payload)


def test_spend_from_zero_delta_is_none() -> None:
    assert spend_from(_event()) is None
    assert spend_from(_event(op=Finish(summary="done"))) is None
    assert spend_from(_event(op=Failed(code="max_steps", message="stop"))) is None


def test_spend_from_observe_and_act_are_io() -> None:
    observe = spend_from(
        _event(
            energy_delta_mj=-1,
            energy_reason="io",
            op=Observe(address="IN.txt", size_bytes=5, content_hash="aa"),
        )
    )
    assert observe is not None
    assert observe.amount == Energy(1)
    assert observe.reason == "io"
    assert observe.meta == {}

    act = spend_from(
        _event(
            energy_delta_mj=-2,
            energy_reason="io",
            op=Act(tool="write_file", address="OUT.md", digest="ab", size_bytes=1),
        )
    )
    assert act is not None
    assert act.amount == Energy(2)
    assert act.reason == "io"


def test_spend_from_plan_and_failed_include_tokens() -> None:
    plan = spend_from(
        _event(
            energy_delta_mj=-5,
            energy_reason="plan",
            op=Plan(provider="null", tokens_in=2, tokens_out=3, summary="p"),
        )
    )
    assert plan is not None
    assert plan.reason == "plan"
    assert plan.amount == Energy(5)
    assert plan.meta == {"tokens_in": 2, "tokens_out": 3}

    failed = spend_from(
        _event(
            energy_delta_mj=-4,
            energy_reason="plan",
            op=Failed(
                code="energy_exhausted",
                message="x",
                attempted_mj=9,
                tokens_in=1,
                tokens_out=1,
            ),
        )
    )
    assert failed is not None
    assert failed.reason == "plan"
    assert failed.amount == Energy(4)
    assert failed.meta == {"tokens_in": 1, "tokens_out": 1}


def test_i1_remaining_non_negative() -> None:
    events = [_event()]
    ok = AccountView(cap=Energy(10), spent=Energy(10), remaining=Energy(0))
    assert all(code != "I1" for code, _ in invariant_issues(events, ok))
    # Energy forbids negative mj; I1 still guards a duck-typed account.
    fake = SimpleNamespace(
        remaining=SimpleNamespace(mj=-1),
        cap=SimpleNamespace(mj=10),
        spent=SimpleNamespace(mj=11),
    )
    codes = [code for code, _ in invariant_issues(events, fake)]  # type: ignore[arg-type]
    assert "I1" in codes


def test_i2_conservation() -> None:
    events = [_event()]
    bad = AccountView(cap=Energy(10), spent=Energy(1), remaining=Energy(8))
    assert any(code == "I2" for code, _ in invariant_issues(events, bad))


def test_i3_spend_from_sum() -> None:
    init = _event()
    observe = _event(
        id="01ARZ3NDEKTSV4RRFFQ69G5FAW",
        seq=2,
        parent=init.id,
        energy_delta_mj=-1,
        energy_reason="io",
        op=Observe(address=".", size_bytes=0, content_hash=None, listing=True),
    )
    events = [init, observe]
    ok = AccountView(cap=Energy(10), spent=Energy(1), remaining=Energy(9))
    assert invariant_issues(events, ok) == []
    drifted = AccountView(cap=Energy(10), spent=Energy(2), remaining=Energy(8))
    assert any(code == "I3" for code, _ in invariant_issues(events, drifted))


def test_i4_unique_ids() -> None:
    first = _event()
    dup = _event(seq=2, parent=first.id)
    account = AccountView(cap=Energy(10), spent=Energy(0), remaining=Energy(10))
    assert any(code == "I4" for code, _ in invariant_issues([first, dup], account))


def test_i5_seq_is_contiguous() -> None:
    first = _event()
    skipped = _event(id="01ARZ3NDEKTSV4RRFFQ69G5FAW", seq=3, parent=first.id)
    account = AccountView(cap=Energy(10), spent=Energy(0), remaining=Energy(10))
    assert any(code == "I5" for code, _ in invariant_issues([first, skipped], account))


def test_i6_parent_chain() -> None:
    first = _event()
    second = _event(id="01ARZ3NDEKTSV4RRFFQ69G5FAW", seq=2, parent="nope")
    account = AccountView(cap=Energy(10), spent=Energy(0), remaining=Energy(10))
    assert any(code == "I6" for code, _ in invariant_issues([first, second], account))
    genesis_parent = _event(parent="01ARZ3NDEKTSV4RRFFQ69G5FAW")
    assert any(code == "I6" for code, _ in invariant_issues([genesis_parent], account))


def test_i7_jail_predicate() -> None:
    init = _event()
    observe = _event(
        id="01ARZ3NDEKTSV4RRFFQ69G5FAW",
        seq=2,
        parent=init.id,
        energy_delta_mj=-1,
        energy_reason="io",
        op=Observe(address="../secret", size_bytes=0, content_hash=None),
    )
    account = AccountView(cap=Energy(10), spent=Energy(1), remaining=Energy(9))
    ok = invariant_issues([init, observe], account, in_jail=lambda _addr: True)
    assert all(code != "I7" for code, _ in ok)
    bad = invariant_issues([init, observe], account, in_jail=lambda _addr: False)
    assert any(code == "I7" for code, _ in bad)
