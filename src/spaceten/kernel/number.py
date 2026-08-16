from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import NewType

import ulid

Id = NewType("Id", str)  # ULID
Seq = NewType("Seq", int)  # monotonic per world, starts at 1

IdFactory = Callable[[], Id]


def new_id() -> Id:
    return Id(str(ulid.new()))


@dataclass(frozen=True, slots=True)
class Quantity:
    value: Decimal
    unit: str  # "mj" | "count" | "byte"

    def __post_init__(self) -> None:
        if self.value < 0:
            raise ValueError("quantities are non-negative; use directed Spend")
