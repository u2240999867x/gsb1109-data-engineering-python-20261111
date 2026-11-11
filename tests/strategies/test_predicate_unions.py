"""Tests for the declarative predicate union strategy."""

from dataclasses import dataclass
from typing import Literal, Optional, Union

import pytest
from attrs import define, fields

from cattrs import BaseConverter, Converter
from cattrs.errors import (
    AmbiguousUnionError,
    UnionDisambiguationError,
    UnionTagConflictError,
    UnionTagMissingError,
    UnionTagUnknownError,
)
from cattrs.gen import make_dict_structure_fn, override
from cattrs.strategies import configure_predicate_union


@pytest.mark.parametrize("converter_cls", [BaseConverter, Converter])
def test_literal_markers_roundtrip(converter_cls):
    """Shared payloads are routed purely on the declared literal marker."""
    converter = converter_cls()

    @define
    class Created:
        kind: Literal["created"]
        id: int

    @define
    class Updated:
        kind: Literal["updated"]
        id: int

    union = Union[Created, Updated]
    configure_predicate_union(union, converter)

    assert converter.structure({"kind": "created", "id": 1}, union) == Created(
        "created", 1
    )
    assert converter.structure({"kind": "updated", "id": 2}, union) == Updated(
        "updated", 2
    )


@pytest.mark.parametrize("converter_cls", [BaseConverter, Converter])
def test_mutually_exclusive_fields_and_fallback(converter_cls):
    """Unique required fields select a member; one unpinned class is fallback."""
    converter = converter_cls()

    @define
    class WithP:
        p: int

    @define
    class WithQ:
        q: int

    @define
    class Empty:
        pass

    union = Union[WithP, WithQ, Empty]
    configure_predicate_union(union, converter)

    assert converter.structure({"p": 1}, union) == WithP(1)
    assert converter.structure({"q": 2}, union) == WithQ(2)
    assert converter.structure({}, union) == Empty()


def test_missing_tag_distinct_from_conflict():
    """Missing tags and conflicting tags raise different error types."""
    converter = Converter()

    @define
    class H:
        a: Literal[1]

    @define
    class J:
        a: Literal[0, 1]

    @define
    class K:
        a: Literal[0]

    union = Union[H, J, K]
    configure_predicate_union(union, converter)

    with pytest.raises(UnionTagMissingError) as missing:
        converter.structure({}, union)
    assert set(missing.value.tag_values) == {0, 1}

    with pytest.raises(UnionTagConflictError) as conflict:
        converter.structure({"a": 1}, union)
    assert set(conflict.value.candidates) == {H, J}

    with pytest.raises(UnionTagConflictError):
        converter.structure({"a": 0}, union)

    # Both are disambiguation errors, but never the same as each other.
    assert not isinstance(missing.value, UnionTagConflictError)
    assert not isinstance(conflict.value, UnionTagMissingError)


def test_unknown_tag_is_distinct():
    """A present tag value matching no member raises its own error."""
    converter = Converter()

    @define
    class A:
        kind: Literal["a"]

    @define
    class B:
        kind: Literal["b"]

    union = Union[A, B]
    configure_predicate_union(union, converter)

    with pytest.raises(UnionTagUnknownError) as exc:
        converter.structure({"kind": "z"}, union)
    assert exc.value.tag_name == "kind"
    assert exc.value.tag_value == "z"


def test_ambiguity_without_fallback():
    """No unique marker and no fallback member is an ambiguity, not a tag miss."""
    converter = Converter()

    @define
    class P:
        p: int

    @define
    class Q:
        q: int

    union = Union[P, Q]
    configure_predicate_union(union, converter)

    with pytest.raises(AmbiguousUnionError) as exc:
        converter.structure({}, union)
    assert set(exc.value.candidates) == {P, Q}


def test_ambiguity_on_several_unique_markers():
    """Payload carrying markers of several members is ambiguous."""
    converter = Converter()

    @define
    class P:
        p: int

    @define
    class Q:
        q: int

    @define
    class R:
        pass

    union = Union[P, Q, R]
    configure_predicate_union(union, converter)

    with pytest.raises(AmbiguousUnionError) as exc:
        converter.structure({"p": 1, "q": 2}, union)
    assert set(exc.value.candidates) == {P, Q}


def test_failed_candidates_have_no_hook_side_effects():
    """Marker evaluation never materializes or runs a rejected member's hook."""
    converter = Converter()
    materialized = []

    @define
    class X:
        x: int

    @define
    class Y:
        y: int

    def factory(cl):
        materialized.append(cl)

        def hook(val, _cl=cl):
            return _cl(val[next(iter(val))])

        return hook

    converter.register_structure_hook_factory(lambda t: t in (X, Y), factory)

    union = Union[X, Y]
    configure_predicate_union(union, converter)

    # Configuring the union does not materialize member hooks.
    assert materialized == []

    assert converter.structure({"x": 5}, union) == X(5)
    assert materialized == [X]

    # An ambiguous payload raises before any hook is touched.
    materialized.clear()
    with pytest.raises(AmbiguousUnionError):
        converter.structure({}, union)
    assert materialized == []


def test_selected_member_custom_hook_runs_once():
    """A custom hook on the selected member is invoked exactly once."""
    converter = Converter()
    calls = []

    @define
    class X:
        x: int

    @define
    class Y:
        y: int

    def hook_x(val, cl):
        calls.append(val)
        return X(val["x"] * 10)

    converter.register_structure_hook(X, hook_x)

    union = Union[X, Y]
    configure_predicate_union(union, converter)

    assert converter.structure({"x": 5}, union) == X(50)
    assert calls == [{"x": 5}]

    # The resolved hook is reused, not regenerated, on subsequent payloads.
    converter.structure({"x": 6}, union)
    assert len(calls) == 2


def test_optional_none_member():
    """``None`` members short-circuit without invoking disambiguation."""
    converter = Converter()

    @define
    class P:
        p: int

    union = Optional[P]
    configure_predicate_union(union, converter)

    assert converter.structure(None, union) is None
    assert converter.structure({"p": 3}, union) == P(3)


def test_dataclasses_supported():
    """The strategy works for dataclass union members too."""
    converter = Converter()

    @dataclass
    class M:
        m: int

    @dataclass
    class N:
        n: int

    union = Union[M, N]
    configure_predicate_union(union, converter)

    assert converter.structure({"m": 1}, union) == M(1)
    assert converter.structure({"n": 2}, union) == N(2)


def test_renamed_marker_fields_supported():
    """Rename overrides on member hooks apply to marker detection."""
    converter = Converter()

    @define
    class E:
        kind: Literal["e"]

    @define
    class F:
        kind: Literal["f"]

    for cl in (E, F):
        converter.register_structure_hook(
            cl, make_dict_structure_fn(cl, converter, kind=override(rename="type"))
        )

    union = Union[E, F]
    configure_predicate_union(union, converter)

    assert converter.structure({"type": "e"}, union) == E("e")
    assert converter.structure({"type": "f"}, union) == F("f")


def test_converter_level_type_overrides_visible_without_generation():
    """Converter-level field renames are seen without generating member hooks."""
    generated = []

    @define
    class ItemOne:
        i: int
        label: str

    @define
    class ItemTwo:
        j: int
        label: str

    converter = Converter(type_overrides={str: override(rename="lbl")})

    def factory(cl):
        generated.append(cl)
        attrib_overrides = {
            a.name: converter.type_overrides[a.type]
            for a in fields(cl)
            if a.type in converter.type_overrides
        }
        return make_dict_structure_fn(cl, converter, **attrib_overrides)

    converter.register_structure_hook_factory(
        lambda t: t in (ItemOne, ItemTwo), factory
    )

    union = Union[ItemOne, ItemTwo]
    configure_predicate_union(union, converter)

    # Configuring the union generated no member hooks.
    assert generated == []
    assert converter.structure({"i": 1, "lbl": "x"}, union) == ItemOne(1, "x")
    assert converter.structure({"j": 2, "lbl": "y"}, union) == ItemTwo(2, "y")
    assert generated == [ItemOne, ItemTwo]


def test_use_literals_false_falls_back_to_fields():
    """Disabling literal markers routes on mutually exclusive fields."""
    converter = Converter()

    @define
    class C:
        b: int
        a: Literal["a"] = "a"

    @define
    class D:
        a: Literal["b"] = "b"

    union = Union[C, D]
    configure_predicate_union(union, converter, use_literals=False)

    assert converter.structure({"a": "a", "b": 1}, union) == C(1, "a")
    # No unique field present; D is the single fallback and its own hook runs.
    assert converter.structure({"a": "b"}, union) == D("b")


def test_non_mapping_payload():
    """Non-mapping payloads fail with a ValueError at the disambiguator."""
    converter = Converter()

    @define
    class X:
        x: int

    @define
    class Y:
        y: int

    union = Union[X, Y]
    configure_predicate_union(union, converter)

    with pytest.raises(ValueError):
        converter.structure([1, 2], union)


def test_all_errors_share_base():
    """Every failure mode is catchable as a union disambiguation error."""
    converter = Converter()

    @define
    class A:
        kind: Literal["a"]

    @define
    class B:
        kind: Literal["b"]

    union = Union[A, B]
    configure_predicate_union(union, converter)

    for payload in ({}, {"kind": "z"}):
        with pytest.raises(UnionDisambiguationError):
            converter.structure(payload, union)


def test_sequential_default_entry_still_available():
    """The converter's built-in union handling remains usable alongside it."""
    converter = Converter()

    @define
    class X:
        x: int

    @define
    class Y:
        y: int

    union = Union[X, Y]

    # Default, pre-strategy behavior keeps working.
    assert converter.structure({"x": 1}, union) == X(1)
    assert converter.structure({"y": 2}, union) == Y(2)

    configure_predicate_union(union, converter)
    assert converter.structure({"x": 3}, union) == X(3)
