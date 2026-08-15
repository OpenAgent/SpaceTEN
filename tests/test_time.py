from datetime import UTC, datetime, timedelta, timezone

import pytest

from spaceten.kernel.time import Clock, FrozenClock


def test_clock_now_is_timezone_aware_utc() -> None:
    instant = Clock().now()
    assert instant.tzinfo is not None
    assert instant.utcoffset() == timedelta(0)


def test_frozen_clock_returns_injected_instant() -> None:
    instant = datetime(2026, 8, 15, 17, 0, 0, tzinfo=UTC)
    clock: Clock = FrozenClock(instant)
    assert clock.now() == instant
    assert clock.now() == clock.now()


def test_clock_accepts_injected_now() -> None:
    instant = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    clock = Clock(now=lambda: instant)
    assert clock.now() == instant


def test_frozen_clock_normalizes_non_utc() -> None:
    offset = timezone(timedelta(hours=-5))
    instant = datetime(2026, 8, 15, 12, 0, 0, tzinfo=offset)
    clock = FrozenClock(instant)
    got = clock.now()
    assert got == instant.astimezone(UTC)
    assert got.tzinfo == UTC


def test_frozen_clock_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        FrozenClock(datetime(2026, 8, 15, 17, 0, 0))


def test_clock_rejects_naive_injected_now() -> None:
    clock = Clock(now=lambda: datetime(2026, 8, 15, 17, 0, 0))
    with pytest.raises(ValueError, match="timezone-aware"):
        clock.now()


def test_clock_has_no_seq_or_parent() -> None:
    clock = Clock()
    assert not hasattr(clock, "seq")
    assert not hasattr(clock, "parent")
    assert clock.now()  # wall time only
