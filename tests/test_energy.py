import pytest

from spaceten.kernel.energy import (
    MIN_COMPLETION_MJ,
    PER_CALL_CEILING_MJ,
    AccountView,
    Energy,
    EnergyExhausted,
    Spend,
    _Account,
    io_cost_mj,
)
from spaceten.kernel.number import new_id


def test_energy_rejects_negative() -> None:
    with pytest.raises(ValueError, match="magnitude"):
        Energy(-1)


def test_energy_accepts_zero_and_positive() -> None:
    assert Energy(0).mj == 0
    assert Energy(MIN_COMPLETION_MJ).mj == 8


def test_energy_is_frozen() -> None:
    energy = Energy(1)
    with pytest.raises(AttributeError):
        energy.mj = 2  # type: ignore[misc]


def test_completion_bounds() -> None:
    assert MIN_COMPLETION_MJ == 8
    assert PER_CALL_CEILING_MJ == 8_000


def test_account_view_conserved() -> None:
    view = AccountView(cap=Energy(10), spent=Energy(4), remaining=Energy(6))
    assert view.conserved()
    assert view.cap.mj == view.spent.mj + view.remaining.mj


def test_account_view_not_conserved() -> None:
    view = AccountView(cap=Energy(10), spent=Energy(4), remaining=Energy(5))
    assert not view.conserved()


def test_debit_conserves() -> None:
    account = _Account(cap=Energy(10), spent=Energy(0), remaining=Energy(10))
    account.debit(Energy(4))
    view = account.view()
    assert view.spent == Energy(4)
    assert view.remaining == Energy(6)
    assert view.cap == Energy(10)
    assert view.conserved()
    assert view.cap.mj == view.spent.mj + view.remaining.mj


def test_debit_exact_remaining_conserves() -> None:
    account = _Account(cap=Energy(10), spent=Energy(6), remaining=Energy(4))
    account.debit(Energy(4))
    view = account.view()
    assert view.spent == Energy(10)
    assert view.remaining == Energy(0)
    assert view.conserved()


def test_debit_overflow_raises_without_mutating() -> None:
    account = _Account(cap=Energy(10), spent=Energy(3), remaining=Energy(7))
    with pytest.raises(EnergyExhausted) as excinfo:
        account.debit(Energy(8))
    err = excinfo.value
    assert err.remaining == Energy(7)
    assert err.requested == Energy(8)
    assert account.spent == Energy(3)
    assert account.remaining == Energy(7)
    assert account.view().conserved()


def test_spend_records_io() -> None:
    spend = Spend(
        event_id=new_id(),
        amount=Energy(1),
        reason="io",
        meta={"op": "read"},
    )
    assert spend.reason == "io"
    assert spend.amount == Energy(1)
    assert spend.meta["op"] == "read"


@pytest.mark.parametrize(
    ("op", "size_bytes", "expected"),
    [
        ("list_dir", 0, 1),
        ("list_dir", 100_000, 1),
        ("observe", 0, 1),
        ("observe", 1, 1),
        ("observe", 4096, 1),
        ("observe", 4097, 2),
        ("read", 8192, 2),
        ("read", 8193, 3),
        ("write", 0, 2),
        ("write", 4096, 2),
        ("write", 4097, 3),
        ("write", 8192, 3),
        ("write", 8193, 4),
        ("check", 0, 0),
        ("status", 999, 0),
        ("log", 0, 0),
        ("init", 0, 0),
    ],
)
def test_io_cost_mj(op: str, size_bytes: int, expected: int) -> None:
    assert io_cost_mj(op, size_bytes) == expected


def test_io_cost_rejects_negative_size() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        io_cost_mj("read", -1)


def test_io_cost_unknown_op() -> None:
    with pytest.raises(ValueError, match="unknown I/O op"):
        io_cost_mj("delete", 0)
