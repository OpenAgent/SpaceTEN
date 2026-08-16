from dataclasses import dataclass
from typing import Literal

from spaceten.kernel.number import Id

MIN_COMPLETION_MJ = 8
PER_CALL_CEILING_MJ = 8_000

_BLOCK_BYTES = 4096


@dataclass(frozen=True, slots=True)
class Energy:
    mj: int

    def __post_init__(self) -> None:
        if self.mj < 0:
            raise ValueError("Energy is a magnitude; Spend carries direction")


@dataclass(frozen=True, slots=True)
class Spend:
    event_id: Id
    amount: Energy
    reason: Literal["io", "plan"]
    meta: dict[str, object]


@dataclass(frozen=True, slots=True)
class AccountView:
    cap: Energy
    spent: Energy
    remaining: Energy

    def conserved(self) -> bool:
        return self.cap.mj == self.spent.mj + self.remaining.mj


class EnergyExhausted(Exception):
    """Debit exceeded remaining energy."""

    def __init__(self, remaining: Energy, requested: Energy) -> None:
        self.remaining = remaining
        self.requested = requested
        super().__init__(
            f"requested {requested.mj} mj exceeds remaining {remaining.mj} mj"
        )


@dataclass
class _Account:
    cap: Energy
    spent: Energy
    remaining: Energy

    def debit(self, amount: Energy) -> None:
        if amount.mj > self.remaining.mj:
            raise EnergyExhausted(self.remaining, amount)
        self.spent = Energy(self.spent.mj + amount.mj)
        self.remaining = Energy(self.remaining.mj - amount.mj)

    def view(self) -> AccountView:
        return AccountView(self.cap, self.spent, self.remaining)


def _blocks_4kib(size_bytes: int) -> int:
    """Count of 4 KiB blocks, treating an empty payload as the first block."""
    if size_bytes <= _BLOCK_BYTES:
        return 1
    extra = size_bytes - _BLOCK_BYTES
    return 1 + (extra + _BLOCK_BYTES - 1) // _BLOCK_BYTES


def io_cost_mj(op: str, size_bytes: int) -> int:
    if size_bytes < 0:
        raise ValueError("size_bytes must be non-negative")
    if op == "list_dir":
        return 1
    if op in ("observe", "read"):
        return _blocks_4kib(size_bytes)
    if op == "write":
        # first 4 KiB is 2 mj; each additional 4 KiB is +1
        return 1 + _blocks_4kib(size_bytes)
    if op in ("check", "status", "log", "init"):
        return 0
    raise ValueError(f"unknown I/O op: {op}")
