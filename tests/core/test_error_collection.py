from dataclasses import dataclass, field, InitVar
from typing import Any, Dict, FrozenSet, List, Optional, Tuple, Union

import pytest

from dacite import (
    Config,
    DaciteAggregateError,
    DaciteError,
    DaciteFieldError,
    ForwardReferenceError,
    MissingValueError,
    StrictUnionMatchError,
    UnexpectedDataError,
    UnionMatchError,
    WrongTypeError,
    from_dict,
    from_dict_collecting_errors,
)


def _paths(error: DaciteAggregateError) -> List[Any]:
    return [item.field_path for item in error.errors]


def _types(error: DaciteAggregateError) -> List[type]:
    return [type(item) for item in error.errors]


def test_collecting_errors_returns_instance_for_valid_data():
    @dataclass
    class X:
        s: str
        i: int

    result = from_dict_collecting_errors(X, {"s": "test", "i": 1})

    assert result == X(s="test", i=1)


def test_collecting_errors_gathers_missing_values_and_wrong_types():
    @dataclass
    class X:
        s: str
        i: int
        f: float

    with pytest.raises(DaciteAggregateError) as exception_info:
        from_dict_collecting_errors(X, {"s": 1, "i": "wrong"})

    assert _types(exception_info.value) == [WrongTypeError, WrongTypeError, MissingValueError]
    assert _paths(exception_info.value) == ["s", "i", "f"]
    assert exception_info.value.errors[0].field_type == str
    assert exception_info.value.errors[0].value == 1


def test_collecting_errors_is_a_dacite_error_with_field_errors():
    @dataclass
    class X:
        i: int

    with pytest.raises(DaciteAggregateError) as exception_info:
        from_dict_collecting_errors(X, {})

    assert isinstance(exception_info.value, DaciteError)
    assert all(isinstance(item, DaciteFieldError) for item in exception_info.value.errors)
    assert "1 error(s)" in str(exception_info.value)
    assert 'missing value for field "i"' in str(exception_info.value)


def test_collecting_errors_keeps_going_after_first_invalid_field():
    @dataclass
    class X:
        a: int
        b: int
        c: int

    with pytest.raises(DaciteAggregateError) as exception_info:
        from_dict_collecting_errors(X, {"a": "x", "b": "y", "c": "z"})

    assert len(exception_info.value.errors) == 3
    assert _paths(exception_info.value) == ["a", "b", "c"]


def test_collecting_errors_collects_nested_record_errors_with_paths():
    @dataclass
    class Inner:
        i: int
        name: str

    @dataclass
    class Outer:
        inner: Inner
        ok: str

    with pytest.raises(DaciteAggregateError) as exception_info:
        from_dict_collecting_errors(Outer, {"inner": {"i": "bad"}, "ok": "fine"})

    assert _types(exception_info.value) == [WrongTypeError, MissingValueError]
    assert _paths(exception_info.value) == ["inner.i", "inner.name"]


def test_collecting_errors_collects_errors_from_multiple_nested_records():
    @dataclass
    class Inner:
        i: int

    @dataclass
    class Outer:
        first: Inner
        second: Inner

    with pytest.raises(DaciteAggregateError) as exception_info:
        from_dict_collecting_errors(Outer, {"first": {"i": "a"}, "second": {"i": "b"}})

    assert _paths(exception_info.value) == ["first.i", "second.i"]


def test_collecting_errors_indexes_list_items():
    @dataclass
    class Item:
        i: int

    @dataclass
    class Holder:
        items: List[Item]

    with pytest.raises(DaciteAggregateError) as exception_info:
        from_dict_collecting_errors(Holder, {"items": [{"i": 1}, {"i": "bad"}, {}]})

    assert _paths(exception_info.value) == ["items[1].i", "items[2].i"]


def test_collecting_errors_indexes_tuple_items():
    @dataclass
    class Holder:
        pair: Tuple[int, str]
        variadic: Tuple[int, ...]

    with pytest.raises(DaciteAggregateError) as exception_info:
        from_dict_collecting_errors(Holder, {"pair": ("bad", 1), "variadic": (1, "x", 3)})

    assert _paths(exception_info.value) == ["pair[0]", "pair[1]", "variadic[1]"]


def test_collecting_errors_indexes_mapping_keys():
    @dataclass
    class Item:
        i: int

    @dataclass
    class Holder:
        table: Dict[str, Item]

    with pytest.raises(DaciteAggregateError) as exception_info:
        from_dict_collecting_errors(Holder, {"table": {"a": {"i": 1}, "b": {"i": "bad"}}})

    assert _paths(exception_info.value) == ["table['b'].i"]


def test_collecting_errors_reports_wrong_scalar_collection_item():
    @dataclass
    class Holder:
        values: List[int]

    with pytest.raises(DaciteAggregateError) as exception_info:
        from_dict_collecting_errors(Holder, {"values": [1, "bad", 3]})

    assert _types(exception_info.value) == [WrongTypeError]
    assert _paths(exception_info.value) == ["values[1]"]


def test_collecting_errors_nested_collection_paths():
    @dataclass
    class Item:
        i: int

    @dataclass
    class Holder:
        matrix: List[List[Item]]

    with pytest.raises(DaciteAggregateError) as exception_info:
        from_dict_collecting_errors(Holder, {"matrix": [[{"i": 1}], [{"i": "bad"}]]})

    assert _paths(exception_info.value) == ["matrix[1][0].i"]


def test_collecting_errors_materializes_containers_on_success():
    @dataclass
    class Item:
        i: int

    @dataclass
    class Holder:
        items: List[Item]
        pair: Tuple[Item, str]
        table: Dict[str, Item]
        frozen: FrozenSet[int]
        optional: Optional[Item]

    result = from_dict_collecting_errors(
        Holder,
        {
            "items": [{"i": 1}],
            "pair": ({"i": 2}, "x"),
            "table": {"k": {"i": 3}},
            "frozen": frozenset({4}),
            "optional": None,
        },
    )

    assert result == Holder(
        items=[Item(1)],
        pair=(Item(2), "x"),
        table={"k": Item(3)},
        frozen=frozenset({4}),
        optional=None,
    )
    assert isinstance(result.items[0], Item)
    assert isinstance(result.pair[0], Item)
    assert isinstance(result.table["k"], Item)


def test_collecting_errors_still_detects_wrong_container_type():
    @dataclass
    class Holder:
        frozen: FrozenSet[int]

    with pytest.raises(DaciteAggregateError) as exception_info:
        from_dict_collecting_errors(Holder, {"frozen": [1, 2]})

    assert _types(exception_info.value) == [WrongTypeError]
    assert _paths(exception_info.value) == ["frozen"]


def test_collecting_errors_default_values_are_not_errors():
    @dataclass
    class X:
        required: int
        with_default: str = "d"
        optional: Optional[int] = None

    result = from_dict_collecting_errors(X, {"required": 1})

    assert result == X(required=1, with_default="d", optional=None)


def test_collecting_errors_strict_collects_unexpected_keys():
    @dataclass
    class X:
        s: str

    with pytest.raises(DaciteAggregateError) as exception_info:
        from_dict_collecting_errors(X, {"s": 1, "extra": 2}, Config(strict=True))

    assert _types(exception_info.value) == [UnexpectedDataError, WrongTypeError]
    assert exception_info.value.errors[0].keys == {"extra"}


def test_collecting_errors_strict_prefixes_nested_unexpected_keys():
    @dataclass
    class Inner:
        i: int

    @dataclass
    class Outer:
        inner: Inner

    with pytest.raises(DaciteAggregateError) as exception_info:
        from_dict_collecting_errors(Outer, {"inner": {"i": 1, "rogue": 2}}, Config(strict=True))

    assert _types(exception_info.value) == [UnexpectedDataError]
    assert _paths(exception_info.value) == ["inner"]


def test_collecting_errors_reports_union_mismatch():
    @dataclass
    class X:
        u: Union[int, str]

    with pytest.raises(DaciteAggregateError) as exception_info:
        from_dict_collecting_errors(X, {"u": 1.0})

    assert _types(exception_info.value) == [UnionMatchError]
    assert _paths(exception_info.value) == ["u"]


def test_collecting_errors_union_of_dataclasses_still_matches():
    @dataclass
    class A:
        a: int

    @dataclass
    class B:
        b: str

    @dataclass
    class X:
        u: Union[A, B]

    result = from_dict_collecting_errors(X, {"u": {"b": "test"}})

    assert result == X(u=B(b="test"))


def test_collecting_errors_union_branch_failures_are_not_collected():
    @dataclass
    class A:
        a: int

    @dataclass
    class B:
        b: str

    @dataclass
    class X:
        u: Union[A, B]

    with pytest.raises(DaciteAggregateError) as exception_info:
        from_dict_collecting_errors(X, {"u": {"a": "bad", "b": 1}})

    assert _types(exception_info.value) == [UnionMatchError]
    assert _paths(exception_info.value) == ["u"]


def test_collecting_errors_strict_union_match():
    @dataclass
    class A:
        i: int

    @dataclass
    class B:
        i: int

    @dataclass
    class X:
        u: Union[A, B]

    with pytest.raises(DaciteAggregateError) as exception_info:
        from_dict_collecting_errors(X, {"u": {"i": 1}}, Config(strict_unions_match=True))

    assert _types(exception_info.value) == [StrictUnionMatchError]
    assert _paths(exception_info.value) == ["u"]


def test_collecting_errors_respects_disabled_type_checking():
    @dataclass
    class X:
        a: int
        b: int

    with pytest.raises(DaciteAggregateError) as exception_info:
        from_dict_collecting_errors(X, {"a": "x"}, Config(check_types=False))

    assert _types(exception_info.value) == [MissingValueError]
    assert _paths(exception_info.value) == ["b"]


def test_collecting_errors_constructor_and_post_init_not_called_with_errors():
    constructor_calls = []
    post_init_calls = []

    @dataclass
    class Inner:
        i: int

        def __post_init__(self) -> None:
            post_init_calls.append(("inner", self.i))

    @dataclass
    class Outer:
        inner: Inner
        name: str

        def __post_init__(self) -> None:
            constructor_calls.append(("outer", self.name))

    with pytest.raises(DaciteAggregateError):
        from_dict_collecting_errors(Outer, {"inner": {"i": "bad"}, "name": 1})

    assert constructor_calls == []
    assert post_init_calls == []


def test_collecting_errors_post_init_runs_on_success_bottom_up():
    calls = []

    @dataclass
    class Inner:
        i: int

        def __post_init__(self) -> None:
            calls.append(("inner", self.i))

    @dataclass
    class Outer:
        inner: Inner

        def __post_init__(self) -> None:
            calls.append(("outer", self.inner.i))

    result = from_dict_collecting_errors(Outer, {"inner": {"i": 5}})

    assert calls == [("inner", 5), ("outer", 5)]
    assert result.inner.i == 5


def test_collecting_errors_preserves_post_init_business_exception():
    class BusinessError(Exception):
        pass

    @dataclass
    class X:
        i: int

        def __post_init__(self) -> None:
            raise BusinessError("rule violated")

    with pytest.raises(BusinessError):
        from_dict_collecting_errors(X, {"i": 1})


def test_collecting_errors_preserves_custom_init_business_exception():
    class BusinessError(Exception):
        pass

    @dataclass(init=False)
    class X:
        i: int

        def __init__(self, i: int) -> None:
            raise BusinessError("init failed")

    with pytest.raises(BusinessError):
        from_dict_collecting_errors(X, {"i": 1})


def test_collecting_errors_nested_post_init_receives_real_instance():
    @dataclass
    class Inner:
        i: int

    @dataclass
    class Outer:
        inner: InitVar[Inner]
        doubled: int = field(init=False)

        def __post_init__(self, inner: Inner) -> None:
            assert isinstance(inner, Inner)
            self.doubled = inner.i * 2

    result = from_dict_collecting_errors(Outer, {"inner": {"i": 21}})

    assert result.doubled == 42


def test_collecting_errors_type_hook_exception_propagates_unchanged():
    @dataclass
    class X:
        i: int

    def raise_error(_: Any) -> Any:
        raise KeyError()

    with pytest.raises(KeyError):
        from_dict_collecting_errors(X, {"i": 1}, Config(type_hooks={int: raise_error}))


def test_collecting_errors_forward_reference_error_propagates():
    @dataclass
    class X:
        y: "Y"

    @dataclass
    class Y:
        s: str

    with pytest.raises(ForwardReferenceError):
        from_dict_collecting_errors(X, {"y": {"s": "text"}})


def test_collecting_errors_accepts_prebuilt_instances():
    @dataclass
    class Inner:
        i: int

    @dataclass
    class Outer:
        inner: Inner
        items: List[Inner]

    inner = Inner(i=7)
    result = from_dict_collecting_errors(Outer, {"inner": inner, "items": [inner]})

    assert result == Outer(inner=inner, items=[inner])


def test_collecting_errors_frozen_nested_dataclass():
    @dataclass(frozen=True)
    class Inner:
        i: int

    @dataclass
    class Outer:
        inner: Inner

    result = from_dict_collecting_errors(Outer, {"inner": {"i": 3}})

    assert result == Outer(inner=Inner(3))

    with pytest.raises(DaciteAggregateError) as exception_info:
        from_dict_collecting_errors(Outer, {"inner": {"i": "bad"}})

    assert _paths(exception_info.value) == ["inner.i"]


def test_from_dict_remains_fail_fast_on_first_error():
    @dataclass
    class Inner:
        i: int
        name: str

    @dataclass
    class Outer:
        first: Inner
        second: Inner

    with pytest.raises(WrongTypeError) as exception_info:
        from_dict(Outer, {"first": {"i": "bad"}, "second": {"i": "also"}})

    assert exception_info.value.field_path == "first.i"


def test_from_dict_fail_fast_collection_path_unchanged():
    @dataclass
    class Holder:
        values: List[int]

    with pytest.raises(WrongTypeError) as exception_info:
        from_dict(Holder, {"values": ["bad"]})

    assert exception_info.value.field_path == "values"
