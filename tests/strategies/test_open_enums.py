"""Tests for the `use_open_enums` strategy."""

from enum import Enum

import pytest
from attrs import define

from cattrs import BaseConverter, Converter, UnknownEnumValue
from cattrs.strategies import use_open_enums


class Status(Enum):
    ACTIVE = "active"
    DISABLED = "disabled"


class StatusV2(Enum):
    """A later version of `Status`, with a new member."""

    ACTIVE = "active"
    DISABLED = "disabled"
    ARCHIVED = "archived"


class TypedStatus(Enum):
    _value_: int

    ON = 1
    OFF = 2


@pytest.fixture(params=(BaseConverter, Converter), scope="function")
def open_converter(request):
    converter = request.param()
    use_open_enums(converter, Status, TypedStatus)
    return converter


def test_known_values_structure_to_members(open_converter):
    assert open_converter.structure("active", Status) is Status.ACTIVE
    assert open_converter.structure(1, TypedStatus) is TypedStatus.ON


def test_unknown_values_are_preserved(open_converter):
    res = open_converter.structure("archived", Status)

    assert res == UnknownEnumValue(Status, "archived")
    assert res.value == "archived"
    assert res.enum is Status


def test_unknown_values_are_not_members(open_converter):
    res = open_converter.structure("archived", Status)

    assert not isinstance(res, Status)
    assert res not in list(Status)
    assert res != Status.ACTIVE
    assert res != Status.DISABLED


def test_unknown_values_round_trip(open_converter):
    res = open_converter.structure("archived", Status)

    assert open_converter.unstructure(res) == "archived"
    assert open_converter.unstructure(Status.ACTIVE) == "active"


def test_unknown_values_round_trip_nested(open_converter):
    @define
    class Event:
        status: Status

    event = open_converter.structure({"status": "archived"}, Event)

    assert event.status == UnknownEnumValue(Status, "archived")
    assert open_converter.unstructure(event) == {"status": "archived"}


def test_typed_enum_unknown_values_preserved_as_is(open_converter):
    res = open_converter.structure("3", TypedStatus)

    assert res == UnknownEnumValue(TypedStatus, "3")
    assert open_converter.unstructure(res) == "3"


def test_explicit_reinterpretation(open_converter):
    """Unknown values can be reinterpreted once the member exists."""
    res = open_converter.structure("archived", Status)
    assert isinstance(res, UnknownEnumValue)

    # The model now knows the value; structuring again resolves it.
    use_open_enums(open_converter, StatusV2)
    assert (
        open_converter.structure(UnknownEnumValue(StatusV2, "archived"), StatusV2)
        is StatusV2.ARCHIVED
    )


def test_reinterpretation_of_still_unknown_value(open_converter):
    res = open_converter.structure(UnknownEnumValue(Status, "archived"), Status)

    assert res == UnknownEnumValue(Status, "archived")


def test_reinterpretation_rejects_other_enums(open_converter):
    with pytest.raises(ValueError):
        open_converter.structure(UnknownEnumValue(StatusV2, "archived"), Status)


def test_strict_enums_still_reject_unknown_values():
    converter = BaseConverter()

    with pytest.raises(ValueError):
        converter.structure("archived", Status)


def test_other_enums_on_same_converter_stay_strict(open_converter):
    with pytest.raises(ValueError):
        open_converter.structure("deleted", StatusV2)


def test_unknown_enum_value_equality():
    assert UnknownEnumValue(Status, "x") == UnknownEnumValue(Status, "x")
    assert UnknownEnumValue(Status, "x") != UnknownEnumValue(Status, "y")
    assert UnknownEnumValue(Status, "x") != UnknownEnumValue(StatusV2, "x")
    assert hash(UnknownEnumValue(Status, "x")) == hash(UnknownEnumValue(Status, "x"))
    assert repr(UnknownEnumValue(Status, "x")) == "UnknownEnumValue(Status, 'x')"


def test_non_enums_rejected():
    with pytest.raises(TypeError):
        use_open_enums(BaseConverter(), int)
