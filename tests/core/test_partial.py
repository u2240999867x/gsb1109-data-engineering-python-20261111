from dataclasses import dataclass, field
from typing import Optional

import pytest

from dacite import Config, MISSING, UnexpectedDataError, WrongTypeError, from_dict


def test_partial_missing_required_field_is_missing_sentinel():
    @dataclass
    class X:
        i: int
        s: str

    result = from_dict(X, {"i": 1}, Config(partial=True))

    assert result.i == 1
    assert result.s is MISSING


def test_partial_none_is_distinct_from_missing():
    @dataclass
    class X:
        i: Optional[int]
        s: Optional[str]

    result = from_dict(X, {"i": None}, Config(partial=True))

    assert result.i is None
    assert result.s is MISSING


def test_partial_provided_value_replaces():
    @dataclass
    class X:
        i: Optional[int]

    result = from_dict(X, {"i": 42}, Config(partial=True))

    assert result.i == 42


def test_partial_does_not_use_field_defaults_for_missing_fields():
    @dataclass
    class X:
        i: int = 1
        s: str = field(default_factory=lambda: "default")

    result = from_dict(X, {}, Config(partial=True))

    assert result.i is MISSING
    assert result.s is MISSING


def test_partial_provided_value_overrides_field_default():
    @dataclass
    class X:
        i: int = 1

    result = from_dict(X, {"i": 2}, Config(partial=True))

    assert result.i == 2


def test_partial_nested_object_missing_is_missing_sentinel():
    @dataclass
    class X:
        i: int

    @dataclass
    class Y:
        x: X

    result = from_dict(Y, {}, Config(partial=True))

    assert result.x is MISSING


def test_partial_nested_object_with_empty_dict_is_a_nested_patch():
    @dataclass
    class X:
        i: int
        s: Optional[str]

    @dataclass
    class Y:
        x: X

    result = from_dict(Y, {"x": {}}, Config(partial=True))

    assert result.x == X(i=MISSING, s=MISSING)


def test_partial_nested_object_with_partial_dict():
    @dataclass
    class X:
        i: int
        s: Optional[str]

    @dataclass
    class Y:
        x: X

    result = from_dict(Y, {"x": {"i": 1}}, Config(partial=True))

    assert result.x == X(i=1, s=MISSING)


def test_partial_nested_object_with_none_clears_optional_field():
    @dataclass
    class X:
        i: int

    @dataclass
    class Y:
        x: Optional[X]

    result = from_dict(Y, {"x": None}, Config(partial=True))

    assert result.x is None


def test_partial_nested_optional_missing_is_missing_sentinel_not_none():
    @dataclass
    class X:
        i: int

    @dataclass
    class Y:
        x: Optional[X]

    result = from_dict(Y, {}, Config(partial=True))

    assert result.x is MISSING


def test_partial_missing_collection_differs_from_empty_collection():
    @dataclass
    class X:
        items: list

    missing_result = from_dict(X, {}, Config(partial=True))
    empty_result = from_dict(X, {"items": []}, Config(partial=True))

    assert missing_result.items is MISSING
    assert empty_result.items == []


def test_partial_still_checks_types():
    @dataclass
    class X:
        i: int

    with pytest.raises(WrongTypeError) as exception_info:
        from_dict(X, {"i": "wrong"}, Config(partial=True))

    assert exception_info.value.field_path == "i"


def test_partial_still_runs_type_hooks_for_provided_values():
    @dataclass
    class X:
        s: Optional[str]

    result = from_dict(X, {"s": "TEST"}, Config(partial=True, type_hooks={str: str.lower}))

    assert result.s == "test"


def test_partial_frozen_dataclass():
    @dataclass(frozen=True)
    class X:
        i: int
        s: Optional[str]

    result = from_dict(X, {"i": 1}, Config(partial=True))

    assert result.i == 1
    assert result.s is MISSING


def test_partial_non_init_field():
    @dataclass
    class X:
        i: int
        s: str = field(init=False)

    result = from_dict(X, {"i": 1}, Config(partial=True))

    assert result.i == 1
    assert result.s is MISSING


def test_partial_with_strict_config_rejects_unexpected_keys():
    @dataclass
    class X:
        i: int

    with pytest.raises(UnexpectedDataError):
        from_dict(X, {"j": 1}, Config(partial=True, strict=True))


def test_partial_with_convert_key():
    @dataclass
    class X:
        field_name: str

    def to_camel_case(name: str) -> str:
        head, *tail = name.split("_")
        return head + "".join(part.title() for part in tail)

    config = Config(partial=True, convert_key=to_camel_case)

    assert from_dict(X, {"fieldName": "test"}, config).field_name == "test"
    assert from_dict(X, {}, config).field_name is MISSING


def test_non_partial_mode_keeps_existing_required_and_optional_rules():
    @dataclass
    class X:
        i: int
        s: Optional[str]

    result = from_dict(X, {"i": 1})

    assert result == X(i=1, s=None)
