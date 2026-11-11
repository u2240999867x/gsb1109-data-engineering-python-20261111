from dataclasses import dataclass
from typing import Dict

import pytest

from dacite import Config, KeyConflictError, UnexpectedDataError, from_dict


def to_camel_case(key: str) -> str:
    first_part, *remaining_parts = key.split("_")
    return first_part + "".join(part.title() for part in remaining_parts)


def test_from_dict_with_strict_and_unexpected_key():
    @dataclass
    class X:
        s: str

    with pytest.raises(UnexpectedDataError) as exception_info:
        from_dict(X, {"s": "test", "i": 1}, Config(strict=True))

    assert str(exception_info.value) == 'can not match "i" to any data class field'
    assert exception_info.value.field_path is None
    assert exception_info.value.keys == {"i"}


def test_from_dict_with_strict_and_multiple_unexpected_keys():
    @dataclass
    class X:
        s: str

    with pytest.raises(UnexpectedDataError) as exception_info:
        from_dict(X, {"s": "test", "b": 1, "a": 2}, Config(strict=True))

    assert str(exception_info.value) == 'can not match "a", "b" to any data class field'
    assert exception_info.value.keys == {"a", "b"}


def test_from_dict_with_strict_and_nested_unexpected_key():
    @dataclass
    class Y:
        i: int

    @dataclass
    class X:
        y: Y

    with pytest.raises(UnexpectedDataError) as exception_info:
        from_dict(X, {"y": {"i": 1, "typo": 2}}, Config(strict=True))

    assert exception_info.value.field_path == "y"
    assert exception_info.value.keys == {"typo"}
    assert str(exception_info.value) == 'can not match "typo" to any data class field at field "y"'


def test_from_dict_with_strict_and_deeply_nested_unexpected_key():
    @dataclass
    class Z:
        flag: bool

    @dataclass
    class Y:
        z: Z

    @dataclass
    class X:
        y: Y

    with pytest.raises(UnexpectedDataError) as exception_info:
        from_dict(X, {"y": {"z": {"flag": True, "extra": None}}}, Config(strict=True))

    assert exception_info.value.field_path == "y.z"
    assert exception_info.value.keys == {"extra"}


def test_from_dict_with_strict_and_generic_dict_field_keeps_business_keys():
    @dataclass
    class X:
        name: str
        metadata: Dict[str, str]

    result = from_dict(X, {"name": "n", "metadata": {"any_key": "v", "other": "w"}}, Config(strict=True))

    assert result == X(name="n", metadata={"any_key": "v", "other": "w"})


def test_from_dict_with_strict_and_plain_dict_field_keeps_business_keys():
    @dataclass
    class X:
        payload: dict

    result = from_dict(X, {"payload": {"whatever": 1, "nested": {"k": "v"}}}, Config(strict=True))

    assert result == X(payload={"whatever": 1, "nested": {"k": "v"}})


def test_from_dict_with_strict_and_dict_of_dataclasses_keeps_business_keys():
    @dataclass
    class Item:
        i: int

    @dataclass
    class X:
        items: Dict[str, Item]

    result = from_dict(X, {"items": {"first": {"i": 1}, "second": {"i": 2}}}, Config(strict=True))

    assert result == X(items={"first": Item(i=1), "second": Item(i=2)})


def test_from_dict_with_strict_and_dict_of_dataclasses_still_checks_nested_fields():
    @dataclass
    class Item:
        i: int

    @dataclass
    class X:
        items: Dict[str, Item]

    with pytest.raises(UnexpectedDataError) as exception_info:
        from_dict(X, {"items": {"first": {"i": 1, "typo": 2}}}, Config(strict=True))

    assert exception_info.value.field_path == "items"
    assert exception_info.value.keys == {"typo"}


def test_from_dict_with_strict_and_convert_key():
    @dataclass
    class Person:
        first_name: str

    result = from_dict(Person, {"firstName": "John"}, Config(strict=True, convert_key=to_camel_case))

    assert result == Person(first_name="John")


def test_from_dict_with_strict_and_convert_key_conflict():
    @dataclass
    class X:
        first_name: str
        firstName: str

    with pytest.raises(KeyConflictError) as exception_info:
        from_dict(X, {"firstName": "John"}, Config(strict=True, convert_key=to_camel_case))

    assert exception_info.value.field_path is None
    assert exception_info.value.conflicts == {"firstName": {"first_name", "firstName"}}
    assert str(exception_info.value) == (
        'can not choose a data class field for conflicting input keys: '
        '"firstName" matches fields: firstName, first_name'
    )


def test_from_dict_with_strict_and_nested_convert_key_conflict():
    @dataclass
    class Y:
        first_name: str
        firstName: str

    @dataclass
    class X:
        y: Y

    with pytest.raises(KeyConflictError) as exception_info:
        from_dict(X, {"y": {"firstName": "John"}}, Config(strict=True, convert_key=to_camel_case))

    assert exception_info.value.field_path == "y"
    assert exception_info.value.conflicts == {"firstName": {"first_name", "firstName"}}
    assert str(exception_info.value) == (
        'can not choose a data class field for conflicting input keys: '
        '"firstName" matches fields: firstName, first_name at field "y"'
    )


def test_from_dict_with_strict_conflict_takes_precedence_over_unexpected_key():
    @dataclass
    class X:
        first_name: str
        firstName: str

    with pytest.raises(KeyConflictError):
        from_dict(
            X,
            {"firstName": "John", "unexpected": 1},
            Config(strict=True, convert_key=to_camel_case),
        )


def test_from_dict_with_strict_failure_does_not_mutate_input():
    @dataclass
    class Y:
        i: int

    @dataclass
    class X:
        y: Y

    data = {"y": {"i": 1, "typo": 2}}

    with pytest.raises(UnexpectedDataError):
        from_dict(X, data, Config(strict=True))

    assert data == {"y": {"i": 1, "typo": 2}}


def test_from_dict_without_strict_ignores_unexpected_and_conflicting_keys():
    @dataclass
    class X:
        first_name: str
        firstName: str

    result = from_dict(X, {"firstName": "John", "extra": 1}, Config(convert_key=to_camel_case))

    assert result == X(first_name="John", firstName="John")
