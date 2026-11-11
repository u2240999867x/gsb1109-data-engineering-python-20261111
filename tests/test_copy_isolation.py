"""Copy/cache isolation regression tests.

These tests exercise converters through *nested* records rather than only
singleton values: leaf hooks (for `Decimal`) are looked up through generated
parent-type hooks, so a stale hook or shared cache would surface inside the
nested result instead of on the direct `Decimal` conversion.
"""

from copy import deepcopy
from decimal import Decimal
from typing import Optional, Union

import pytest
from attrs import define, resolve_types

from cattrs import BaseConverter, Converter, UnstructureStrategy
from cattrs.strategies import configure_tagged_union


@define
class Leaf:
    amount: Decimal


@define
class Root:
    leaf: Leaf
    amounts: list[Decimal]
    amounts_map: dict[str, Decimal]
    maybe: Optional[Decimal]


resolve_types(Leaf)
resolve_types(Root)

SAMPLE = Root(
    Leaf(Decimal("1.5")),
    [Decimal("2"), Decimal("3")],
    {"k": Decimal("4")},
    Decimal("7"),
)


def old_rule(value: Decimal) -> str:
    return f"OLD:{value}"


def new_rule(value: Decimal) -> str:
    return f"NEW:{value}"


def third_rule(value: Decimal) -> str:
    return f"THIRD:{value}"


def nested_expected(rule: str) -> dict:
    prefix = f"{rule}:"
    return {
        "leaf": {"amount": f"{prefix}1.5"},
        "amounts": [f"{prefix}2", f"{prefix}3"],
        "amounts_map": {"k": f"{prefix}4"},
        "maybe": f"{prefix}7",
    }


def nested_raw(rule: str) -> dict:
    prefix = f"{rule}:"
    return {
        "leaf": {"amount": f"{prefix}1.5"},
        "amounts": [f"{prefix}2", f"{prefix}3"],
        "amounts_map": {"k": f"{prefix}4"},
        "maybe": f"{prefix}7",
    }


STRUCTURED_SAMPLE = Root(
    Leaf(Decimal("1.5")),
    [Decimal("2"), Decimal("3")],
    {"k": Decimal("4")},
    Decimal("7"),
)


@pytest.mark.parametrize("converter_cls", [BaseConverter, Converter], indirect=True)
@pytest.mark.parametrize(
    "strategy", [UnstructureStrategy.AS_DICT, UnstructureStrategy.AS_TUPLE]
)
def test_copy_decimal_hook_isolation_nested(converter_cls, strategy):
    """A hook registered on the copy only affects the copy's later conversions.

    Parent-type hooks generated before the copy must not pin the old leaf rule
    on the copy, and updating the copy must not change the original.
    """
    original = converter_cls(unstruct_strat=strategy)
    original.register_unstructure_hook(Decimal, old_rule)

    # Generate and cache the parent-type (Root/Leaf/collection) hooks first.
    assert original.unstructure(SAMPLE) is not None

    copied = original.copy()
    copied.register_unstructure_hook(Decimal, new_rule)

    # Interleave conversions on both converters.
    assert copied.unstructure(SAMPLE) == (
        nested_expected("NEW")
        if strategy is UnstructureStrategy.AS_DICT
        else _nested_tuple_expected("NEW")
    )
    assert original.unstructure(SAMPLE) == (
        nested_expected("OLD")
        if strategy is UnstructureStrategy.AS_DICT
        else _nested_tuple_expected("OLD")
    )
    assert copied.unstructure(SAMPLE) == (
        nested_expected("NEW")
        if strategy is UnstructureStrategy.AS_DICT
        else _nested_tuple_expected("NEW")
    )

    # The singleton conversion alone is not sufficient evidence; assert it too.
    assert copied.unstructure(Decimal("9")) == "NEW:9"
    assert original.unstructure(Decimal("9")) == "OLD:9"


def _nested_tuple_expected(rule: str) -> tuple:
    prefix = f"{rule}:"
    return (
        (f"{prefix}1.5",),
        [f"{prefix}2", f"{prefix}3"],
        {"k": f"{prefix}4"},
        f"{prefix}7",
    )


@pytest.mark.parametrize("converter_cls", [BaseConverter, Converter], indirect=True)
def test_original_hook_update_after_copy_does_not_leak(converter_cls):
    """Updating the original after copying leaves the copy on the old rule."""
    original = converter_cls()
    original.register_unstructure_hook(Decimal, old_rule)
    original.unstructure(SAMPLE)

    copied = original.copy()
    original.register_unstructure_hook(Decimal, third_rule)

    assert original.unstructure(SAMPLE) == nested_expected("THIRD")
    assert copied.unstructure(SAMPLE) == nested_expected("OLD")

    copied.register_unstructure_hook(Decimal, new_rule)
    assert copied.unstructure(SAMPLE) == nested_expected("NEW")
    assert original.unstructure(SAMPLE) == nested_expected("THIRD")


@pytest.mark.parametrize("converter_cls", [BaseConverter, Converter], indirect=True)
def test_overwriting_hook_regenerates_parent_hooks(converter_cls):
    """Re-registering a leaf hook invalidates already-generated parent hooks."""
    converter = converter_cls()
    converter.register_unstructure_hook(Decimal, old_rule)
    assert converter.unstructure(SAMPLE) == nested_expected("OLD")

    converter.register_unstructure_hook(Decimal, new_rule)
    assert converter.unstructure(SAMPLE) == nested_expected("NEW")

    converter.register_unstructure_hook(Decimal, old_rule)
    assert converter.unstructure(SAMPLE) == nested_expected("OLD")


@pytest.mark.parametrize("converter_cls", [BaseConverter, Converter], indirect=True)
def test_copy_isolation_with_func_and_factory_hooks(converter_cls):
    """Predicate and factory hook registrations stay copy-local too."""
    func_converter = converter_cls()
    func_converter.register_unstructure_hook_func(
        lambda t: t is Decimal, old_rule
    )
    func_converter.unstructure(SAMPLE)
    func_copy = func_converter.copy()
    func_copy.register_unstructure_hook_func(lambda t: t is Decimal, new_rule)

    assert func_copy.unstructure(SAMPLE) == nested_expected("NEW")
    assert func_converter.unstructure(SAMPLE) == nested_expected("OLD")

    factory_converter = converter_cls()
    factory_converter.register_unstructure_hook_factory(
        lambda t: t is Decimal, lambda _t: old_rule
    )
    factory_converter.unstructure(SAMPLE)
    factory_copy = factory_converter.copy()
    factory_copy.register_unstructure_hook_factory(
        lambda t: t is Decimal, lambda _t: new_rule
    )

    assert factory_copy.unstructure(SAMPLE) == nested_expected("NEW")
    assert factory_converter.unstructure(SAMPLE) == nested_expected("OLD")


@pytest.mark.parametrize("converter_cls", [BaseConverter, Converter], indirect=True)
def test_deepcopy_hook_isolation_nested(converter_cls):
    """``deepcopy`` delegates to ``copy`` and keeps hook state independent."""
    original = converter_cls()
    original.register_unstructure_hook(Decimal, old_rule)
    original.unstructure(SAMPLE)

    copied = deepcopy(original)
    copied.register_unstructure_hook(Decimal, new_rule)

    assert copied.unstructure(SAMPLE) == nested_expected("NEW")
    assert original.unstructure(SAMPLE) == nested_expected("OLD")


@pytest.mark.parametrize("converter_cls", [BaseConverter, Converter], indirect=True)
def test_copy_structure_hook_isolation_nested(converter_cls):
    """Structure hooks for nested records are isolated across copies."""
    original = converter_cls()
    original.register_structure_hook(
        Decimal, lambda value, _: Decimal(str(value).removeprefix("OLD:"))
    )
    assert original.structure(nested_raw("OLD"), Root) == STRUCTURED_SAMPLE

    copied = original.copy()
    copied.register_structure_hook(
        Decimal, lambda value, _: Decimal(str(value).removeprefix("NEW:"))
    )

    assert copied.structure(nested_raw("NEW"), Root) == STRUCTURED_SAMPLE
    assert original.structure(nested_raw("OLD"), Root) == STRUCTURED_SAMPLE
    assert copied.structure(nested_raw("NEW"), Root) == STRUCTURED_SAMPLE

    assert copied.structure("NEW:9", Decimal) == Decimal("9")
    assert original.structure("OLD:9", Decimal) == Decimal("9")


@pytest.mark.parametrize("converter_cls", [BaseConverter, Converter], indirect=True)
def test_copy_preserves_custom_union_structure_hook(converter_cls):
    """Custom union structure hooks live in the union registry and must copy.

    The members share field names, so default union disambiguation cannot
    produce a hook; only the registered hook can structure these values.
    """

    @define
    class MemberA:
        x: int

    @define
    class MemberB:
        x: int

    union = Union[MemberA, MemberB]

    converter = converter_cls()
    converter.register_structure_hook(
        union,
        lambda value, _: MemberA(value["x"])
        if value.get("kind") == "a"
        else MemberB(value["x"]),
    )
    assert converter.structure({"kind": "b", "x": 2}, union) == MemberB(2)

    copied = converter.copy()
    assert union in copied._union_struct_registry
    assert copied.structure({"kind": "b", "x": 2}, union) == MemberB(2)

    # Re-registering on one converter must not touch the other copy.
    copied.register_structure_hook(union, lambda value, _: MemberA(value["x"]))
    assert copied.structure({"kind": "b", "x": 3}, union) == MemberA(3)
    assert converter.structure({"kind": "b", "x": 3}, union) == MemberB(3)


@pytest.mark.parametrize("converter_cls", [BaseConverter, Converter], indirect=True)
def test_copy_preserves_tagged_union_strategy(converter_cls):
    """Tagged unions rely on the union structure registry and survive copying."""

    @define
    class MemberA:
        x: int

    @define
    class MemberB:
        x: int

    union = Union[MemberA, MemberB]

    converter = converter_cls()
    configure_tagged_union(union, converter)

    copied = converter.copy()
    assert copied.structure({"_type": "MemberB", "x": 5}, union) == MemberB(5)


@pytest.mark.parametrize("converter_cls", [BaseConverter, Converter], indirect=True)
def test_copy_preserves_fallback_factories(converter_cls):
    """Custom fallback factories are per-instance state and must be copied."""

    class Unknown:
        pass

    converter = converter_cls(
        unstructure_fallback_factory=lambda _t: (
            lambda value: f"fallback:{value!r}"
        ),
        structure_fallback_factory=lambda _t: (lambda value, _: Unknown()),
    )

    copied = converter.copy()

    assert copied.unstructure(Unknown()).startswith("fallback:")
    assert isinstance(copied.structure("whatever", Unknown), Unknown)


def test_legacy_cattr_package_copy_isolation():
    """The legacy ``cattr`` package exposes the same copy semantics."""
    from cattr import Converter as LegacyConverter

    original = LegacyConverter()
    original.register_unstructure_hook(Decimal, old_rule)
    original.unstructure(SAMPLE)

    copied = original.copy()
    copied.register_unstructure_hook(Decimal, new_rule)

    assert copied.unstructure(SAMPLE) == nested_expected("NEW")
    assert original.unstructure(SAMPLE) == nested_expected("OLD")
