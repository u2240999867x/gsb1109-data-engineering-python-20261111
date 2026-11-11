from dataclasses import dataclass
from typing import Generic, List, TypeVar, get_type_hints

import pytest

from dacite import from_dict, UnboundTypeVarError

from tests.common import pep_604_support

T = TypeVar("T")
U = TypeVar("U")


@dataclass
class X:
    a: str


@dataclass
class A(Generic[T, U]):
    x: T
    y: List[U]


def test_multi_generic():
    data = {
        "x": {
            "a": "foo",
        },
        "y": [1, 2, 3],
    }

    result = from_dict(data_class=A[X, int], data=data)

    assert result == A(x=X(a="foo"), y=[1, 2, 3])

    # assert that the typing object hasn't been modified in-place
    hints = get_type_hints(A)
    assert hints["x"].__class__ is TypeVar


def test_generic_parent():
    @dataclass
    class B(A[X, int]):
        z: str

    data = {
        "x": {
            "a": "foo",
        },
        "y": [1, 2, 3],
        "z": "bar",
    }

    result = from_dict(data_class=B, data=data)

    assert result == B(x=X(a="foo"), y=[1, 2, 3], z="bar")


def test_generic_field():
    @dataclass
    class C:
        z: A[X, int]

    data = {
        "z": {
            "x": {
                "a": "foo",
            },
            "y": [1, 2, 3],
        }
    }

    result = from_dict(data_class=C, data=data)

    assert result == C(z=A(x=X(a="foo"), y=[1, 2, 3]))


@pep_604_support
def test_pep604():
    @dataclass
    class D(Generic[T]):
        x: list[T] | int | None  # pylint: disable=unsupported-binary-operation

    data = {"x": [{"a": "foo"}, {"a": "bar"}]}

    result = from_dict(data_class=D[X], data=data)

    assert result == D[X](x=[X(a="foo"), X(a="bar")])


@dataclass
class Order:
    sku: str


@dataclass
class Shipment:
    tracking_number: str


@dataclass
class BaseRepository(Generic[T]):
    items: List[T]


@dataclass
class OrderRepository(BaseRepository[Order]):
    pass


@dataclass
class AuditedOrderRepository(OrderRepository, Generic[U]):
    audit_log: U


def test_multi_level_generic_inheritance():
    data = {
        "items": [{"sku": "a"}, {"sku": "b"}],
        "audit_log": {"created": "today"},
    }

    result = from_dict(data_class=AuditedOrderRepository[dict], data=data)

    assert result == AuditedOrderRepository[dict](
        items=[Order(sku="a"), Order(sku="b")], audit_log={"created": "today"}
    )
    # nested containers must hold actual dataclass instances, not dicts
    assert all(isinstance(item, Order) for item in result.items)


def test_multi_level_generic_inheritance_with_indirection():
    @dataclass
    class ParameterizedMid(BaseRepository[T], Generic[T]):
        pass

    @dataclass
    class Leaf(ParameterizedMid[Order], Generic[U]):
        extra: U

    data = {"items": [{"sku": "a"}], "extra": 42}

    result = from_dict(data_class=Leaf[int], data=data)

    assert result == Leaf[int](items=[Order(sku="a")], extra=42)
    assert isinstance(result.items[0], Order)


def test_generic_cache_is_isolated_per_type_argument():
    @dataclass
    class OrderRepo(BaseRepository[Order]):
        pass

    @dataclass
    class ShipmentRepo(BaseRepository[Shipment]):
        pass

    order_repo = from_dict(data_class=OrderRepo, data={"items": [{"sku": "a"}]})
    shipment_repo = from_dict(data_class=ShipmentRepo, data={"items": [{"tracking_number": "1Z"}]})
    order_repo_again = from_dict(data_class=OrderRepo, data={"items": [{"sku": "b"}]})

    assert isinstance(order_repo.items[0], Order)
    assert isinstance(shipment_repo.items[0], Shipment)
    assert isinstance(order_repo_again.items[0], Order)
    assert order_repo_again.items[0].sku == "b"


def test_same_generic_class_with_different_arguments_does_not_share_cache():
    first = from_dict(data_class=A[X, int], data={"x": {"a": "foo"}, "y": [1]})
    second = from_dict(data_class=A[X, str], data={"x": {"a": "bar"}, "y": ["baz"]})
    third = from_dict(data_class=A[X, int], data={"x": {"a": "qux"}, "y": [2]})

    assert first == A(x=X(a="foo"), y=[1])
    assert second == A(x=X(a="bar"), y=["baz"])
    assert third == A(x=X(a="qux"), y=[2])


def test_unbound_type_var_is_reported():
    with pytest.raises(UnboundTypeVarError) as exception_info:
        from_dict(data_class=A, data={"x": {"a": "foo"}, "y": [1, 2, 3]})

    assert exception_info.value.field_path == "x"
    assert "T" in str(exception_info.value)


def test_partially_unbound_type_var_is_reported():
    with pytest.raises(UnboundTypeVarError) as exception_info:
        from_dict(data_class=A[X, U], data={"x": {"a": "foo"}, "y": [1, 2, 3]})

    assert exception_info.value.field_path == "y"
    assert "U" in str(exception_info.value)
