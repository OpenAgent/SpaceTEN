from decimal import Decimal

import pytest
import ulid

from spaceten.errors import InvariantError, StaleParent, WorldExists, WorldLocked
from spaceten.kernel.number import Id, IdFactory, Quantity, Seq, new_id

_CROCKFORD32 = frozenset("0123456789ABCDEFGHJKMNPQRSTVWXYZ")


def _assert_ulid_shape(value: str) -> None:
    assert len(value) == 26
    assert all(ch in _CROCKFORD32 for ch in value)
    ulid.parse(value)


def test_new_id_is_ulid_shaped() -> None:
    ident = new_id()
    _assert_ulid_shape(ident)
    assert ident != new_id()


def test_id_factory_is_injectable_and_deterministic() -> None:
    n = 0

    def factory() -> Id:
        nonlocal n
        n += 1
        return Id(str(ulid.from_int(n)))

    injected: IdFactory = factory
    first = injected()
    second = injected()
    assert first != second
    _assert_ulid_shape(first)
    _assert_ulid_shape(second)

    n = 0
    assert factory() == first
    assert factory() == second


def test_seq_is_int_newtype() -> None:
    assert Seq(1) == 1


def test_quantity_rejects_negative() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        Quantity(Decimal("-1"), "mj")


def test_quantity_accepts_zero_and_positive() -> None:
    zero = Quantity(Decimal(0), "mj")
    assert zero.value == Decimal(0)
    assert zero.unit == "mj"
    counted = Quantity(Decimal(10), "count")
    assert counted.value == Decimal(10)
    assert counted.unit == "count"


def test_quantity_is_frozen() -> None:
    qty = Quantity(Decimal(1), "byte")
    with pytest.raises(AttributeError):
        qty.value = Decimal(2)  # type: ignore[misc]


def test_kernel_error_types() -> None:
    for cls in (StaleParent, InvariantError, WorldExists, WorldLocked):
        assert issubclass(cls, Exception)
        assert str(cls("x")) == "x"
