from collections.abc import Callable
from datetime import UTC, datetime


class Clock:
    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        self._now = now

    def now(self) -> datetime:
        """Timezone-aware UTC wall time. Tests inject a frozen clock."""
        if self._now is None:
            return datetime.now(UTC)
        instant = self._now()
        if instant.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return instant.astimezone(UTC)


class FrozenClock(Clock):
    def __init__(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            raise ValueError("frozen clock requires a timezone-aware datetime")
        frozen = instant.astimezone(UTC)
        super().__init__(now=lambda: frozen)
