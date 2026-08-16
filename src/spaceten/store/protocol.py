from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from spaceten.kernel.energy import AccountView
from spaceten.kernel.event import Event
from spaceten.kernel.number import Id
from spaceten.kernel.space import Cell


@dataclass(frozen=True, slots=True, kw_only=True)
class WorldHeader:
    id: Id
    schema: Literal[1] = 1
    created_at: datetime
    root_policy: Literal["jail"] = "jail"
    energy_unit: Literal["mj"] = "mj"
    energy_cap: int


class Store(Protocol):
    def append(self, event: Event) -> None: ...

    def load_events(self) -> list[Event]: ...

    def load_header(self) -> WorldHeader: ...

    def save_header(self, header: WorldHeader) -> None: ...

    def save_caches(self, account: AccountView, cells: dict[str, Cell]) -> None: ...

    def load_caches(self) -> tuple[AccountView, dict[str, Cell]] | None: ...

    def lock(self) -> AbstractContextManager[None]: ...

    def stage_write(self, event_id: Id, dest: Path, data: bytes) -> Path: ...

    def commit_write(self, staged: Path, dest: Path) -> None: ...

    def recover_writes(self, events: list[Event]) -> None: ...
