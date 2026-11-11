from dataclasses import dataclass, InitVar, field
from decimal import Decimal
from typing import Annotated, Generic, List, Optional, TypeVar

import pytest

from dacite import Config, UnregisteredMetadataError, WrongTypeError, from_dict
from dacite.generics import get_concrete_type_hints
from tests.common import annotated_support


class Unit:
    def __init__(self, unit: str):
        self.unit = unit

    def __repr__(self):
        return f"Unit({self.unit!r})"

    def __eq__(self, other):
        return isinstance(other, Unit) and self.unit == other.unit

    def __hash__(self):
        return hash(("Unit", self.unit))


class NonNegative:
    def __repr__(self):
        return "NonNegative()"

    def __eq__(self, other):
        return isinstance(other, NonNegative)

    def __hash__(self):
        return hash("NonNegative")


def to_decimal(value, marker):
    return Decimal(value)


def check_non_negative(value, marker):
    value = Decimal(value)
    if value < 0:
        raise ValueError(f"{value} must be non-negative")
    return value


@annotated_support
def test_from_dict_with_metadata_hook():
    @dataclass
    class X:
        amount: Annotated[Decimal, Unit("USD")]

    config = Config(metadata_hooks={Unit("USD"): to_decimal})

    result = from_dict(X, {"amount": "12.50"}, config)

    assert result == X(amount=Decimal("12.50"))


@annotated_support
def test_metadata_hook_receives_the_marker_instance():
    received = []

    @dataclass
    class X:
        amount: Annotated[Decimal, Unit("USD")]

    def hook(value, marker):
        received.append(marker)
        return value

    from_dict(X, {"amount": Decimal("1")}, Config(metadata_hooks={Unit("USD"): hook}))

    assert received == [Unit("USD")]


@annotated_support
def test_metadata_hook_can_enforce_a_rule():
    @dataclass
    class X:
        amount: Annotated[Decimal, NonNegative()]

    config = Config(metadata_hooks={NonNegative(): check_non_negative})

    with pytest.raises(ValueError):
        from_dict(X, {"amount": "-1"}, config)


@annotated_support
def test_multiple_markers_are_applied_in_annotation_order():
    calls = []

    @dataclass
    class X:
        value: Annotated[int, "first", "second"]

    config = Config(
        metadata_hooks={
            "first": lambda value, marker: (calls.append("first"), value)[1],
            "second": lambda value, marker: (calls.append("second"), value)[1],
        }
    )

    from_dict(X, {"value": 1}, config)

    assert calls == ["first", "second"]


@annotated_support
def test_metadata_hook_runs_before_ordinary_type_hook():
    calls = []

    @dataclass
    class X:
        value: Annotated[int, "marker"]

    config = Config(
        type_hooks={int: lambda value: (calls.append("type_hook"), value + 1)[1]},
        metadata_hooks={"marker": lambda value, marker: (calls.append("metadata_hook"), value * 10)[1]},
    )

    result = from_dict(X, {"value": 3}, config)

    assert result == X(value=31)
    assert calls == ["metadata_hook", "type_hook"]


@annotated_support
def test_metadata_hook_runs_before_cast():
    @dataclass
    class X:
        value: Annotated[int, "marker"]

    config = Config(cast=[int], metadata_hooks={"marker": lambda value, marker: str(value)})

    assert from_dict(X, {"value": 5}, config) == X(value=5)


@annotated_support
def test_unregistered_marker_is_ignored_by_default():
    @dataclass
    class X:
        amount: Annotated[Decimal, Unit("EUR")]

    result = from_dict(X, {"amount": Decimal("1.0")}, Config())

    assert result == X(amount=Decimal("1.0"))


@annotated_support
def test_unregistered_marker_is_rejected_when_policy_is_enabled():
    @dataclass
    class X:
        amount: Annotated[Decimal, Unit("EUR")]

    with pytest.raises(UnregisteredMetadataError) as exception_info:
        from_dict(X, {"amount": Decimal("1.0")}, Config(reject_unregistered_metadata=True))

    error = exception_info.value
    assert error.field_path == "amount"
    assert error.marker == Unit("EUR")
    assert error.field_type == Annotated[Decimal, Unit("EUR")]


@annotated_support
def test_rejection_diagnostic_explains_both_policies():
    @dataclass
    class X:
        value: Annotated[int, "secret"]

    with pytest.raises(UnregisteredMetadataError) as exception_info:
        from_dict(X, {"value": 1}, Config(reject_unregistered_metadata=True))

    assert str(exception_info.value) == (
        'unregistered metadata marker "\'secret\'" found in type "int" of field "value" - either register a hook '
        "for it in Config.metadata_hooks or set Config.reject_unregistered_metadata=False to ignore it"
    )


@annotated_support
def test_rejection_path_is_updated_for_nested_fields():
    @dataclass
    class Inner:
        amount: Annotated[Decimal, Unit("USD")]

    @dataclass
    class Outer:
        inner: Inner

    with pytest.raises(UnregisteredMetadataError) as exception_info:
        from_dict(Outer, {"inner": {"amount": Decimal("1")}}, Config(reject_unregistered_metadata=True))

    assert exception_info.value.field_path == "inner.amount"


@annotated_support
def test_two_decimal_fields_with_different_markers_are_distinct_in_the_type_cache():
    @dataclass
    class Ledger:
        gross: Annotated[Decimal, Unit("USD")]
        net: Annotated[Decimal, NonNegative()]

    hints = get_concrete_type_hints(Ledger, include_extras=True)

    assert hints["gross"] != hints["net"]
    assert hints["gross"].__origin__ is Decimal
    assert hints["net"].__origin__ is Decimal

    config = Config(metadata_hooks={Unit("USD"): to_decimal, NonNegative(): check_non_negative})

    result = from_dict(Ledger, {"gross": "12.50", "net": "3.25"}, config)

    assert result == Ledger(gross=Decimal("12.50"), net=Decimal("3.25"))


@annotated_support
def test_type_check_uses_the_underlying_type():
    @dataclass
    class X:
        amount: Annotated[Decimal, NonNegative()]

    with pytest.raises(WrongTypeError) as exception_info:
        from_dict(X, {"amount": "not-a-decimal"}, Config())

    assert exception_info.value.field_type == Annotated[Decimal, NonNegative()]
    assert "should be \"Decimal\"" in str(exception_info.value)


@annotated_support
def test_optional_annotated_field_accepts_none_without_running_hooks():
    calls = []

    @dataclass
    class X:
        amount: Optional[Annotated[Decimal, NonNegative()]] = None

    config = Config(metadata_hooks={NonNegative(): lambda value, marker: (calls.append(marker), value)[1]})

    assert from_dict(X, {"amount": None}, config) == X(amount=None)
    assert calls == []


@annotated_support
def test_optional_annotated_field_defaults_to_none_when_missing():
    @dataclass
    class X:
        amount: Optional[Annotated[Decimal, NonNegative()]] = None

    assert from_dict(X, {}, Config(metadata_hooks={NonNegative(): check_non_negative})) == X(amount=None)


@annotated_support
def test_annotated_item_type_inside_collection_runs_hook_per_item():
    @dataclass
    class X:
        items: List[Annotated[Decimal, NonNegative()]]

    config = Config(metadata_hooks={NonNegative(): check_non_negative})

    result = from_dict(X, {"items": ["1.5", "2.5"]}, config)

    assert result == X(items=[Decimal("1.5"), Decimal("2.5")])
    with pytest.raises(ValueError):
        from_dict(X, {"items": ["1.5", "-2.5"]}, config)


@annotated_support
def test_annotated_collection_passes_the_whole_collection_to_the_hook():
    seen = []

    @dataclass
    class X:
        items: Annotated[List[Decimal], NonNegative()]

    def hook(value, marker):
        seen.append(value)
        return value

    result = from_dict(X, {"items": [Decimal("1")]}, Config(metadata_hooks={NonNegative(): hook}))

    assert result == X(items=[Decimal("1")])
    assert seen == [[Decimal("1")]]


@annotated_support
def test_unhashable_marker_does_not_break_ignore_policy():
    @dataclass
    class X:
        value: Annotated[int, {"rule": "unknown"}]

    assert from_dict(X, {"value": 1}, Config()) == X(value=1)


@annotated_support
def test_unhashable_marker_is_rejected_without_type_error():
    @dataclass
    class X:
        value: Annotated[int, {"rule": "unknown"}]

    with pytest.raises(UnregisteredMetadataError) as exception_info:
        from_dict(X, {"value": 1}, Config(reject_unregistered_metadata=True))

    assert exception_info.value.marker == {"rule": "unknown"}


@annotated_support
def test_unhashable_marker_inside_nested_generics_does_not_crash():
    from typing import Dict

    @dataclass
    class Y:
        optional: Optional[Annotated[int, {"rule": "x"}]]
        sequence: List[Annotated[int, {"rule": "x"}]]
        mapping: Dict[str, Annotated[int, {"rule": "x"}]]
        wrapped_collection: Annotated[List[int], {"rule": "x"}]

    data = {
        "optional": 1,
        "sequence": [2, 3],
        "mapping": {"k": 4},
        "wrapped_collection": [5],
    }

    assert from_dict(Y, data, Config()) == Y(
        optional=1,
        sequence=[2, 3],
        mapping={"k": 4},
        wrapped_collection=[5],
    )


@annotated_support
def test_string_marker_is_not_mistaken_for_a_forward_reference():
    @dataclass
    class X:
        value: Annotated[int, "not-a-reference"]

    with pytest.raises(UnregisteredMetadataError) as exception_info:
        from_dict(X, {"value": 1}, Config(reject_unregistered_metadata=True))

    assert exception_info.value.marker == "not-a-reference"


@annotated_support
def test_generic_dataclass_keeps_metadata_on_the_concretized_type_var():
    T = TypeVar("T")

    @dataclass
    class Box(Generic[T]):
        item: Annotated[T, Unit("X")]

    result = from_dict(Box[int], {"item": "5"}, Config(metadata_hooks={Unit("X"): lambda value, marker: int(value)}))

    assert result == Box(item=5)


@annotated_support
def test_strict_union_with_unhashable_annotated_member_does_not_crash():
    from typing import Union

    @dataclass
    class X:
        value: Union[Annotated[int, {"rule": "x"}], str]

    result = from_dict(X, {"value": 1}, Config(strict_unions_match=True))

    assert result == X(value=1)


@annotated_support
def test_init_var_wrapping_annotated_runs_metadata_hook():
    @dataclass
    class X:
        a: InitVar[Annotated[int, Unit("X")]]
        b: int = field(init=False)

        def __post_init__(self, a: int) -> None:
            self.b = a

    result = from_dict(X, {"a": "7"}, Config(metadata_hooks={Unit("X"): lambda value, marker: int(value)}))

    assert result.b == 7
