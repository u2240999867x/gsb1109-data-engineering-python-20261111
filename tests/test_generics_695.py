"""Tests for PEP 695 (Type Parameter Syntax)."""

from dataclasses import dataclass, field

import pytest
from attrs import Factory, define

from cattrs import BaseConverter, Converter
from cattrs.errors import ClassValidationError, StructureHandlerNotFoundError

from ._compat import is_py312_plus


@define
class TreeNode[T]:
    value: T
    children: list["Tree[T]"] = Factory(list)


type Tree[T] = TreeNode[T]


@dataclass
class TreeDataclass[T]:
    value: T
    children: list["DataclassTree[T]"] = field(default_factory=list)


type DataclassTree[T] = TreeDataclass[T]


type SelfReferential[T] = T | list["SelfReferential[T]"]


@pytest.mark.skipif(not is_py312_plus, reason="3.12+ syntax")
def test_simple_generic_roundtrip(converter: BaseConverter):
    """PEP 695 attrs generics work."""

    @define
    class A[T]:
        a: T

    assert converter.structure({"a": "1"}, A[int]) == A(1)
    assert converter.unstructure(A(1)) == {"a": 1}

    if isinstance(converter, Converter):
        # Only supported on a Converter
        assert converter.unstructure(A(1), A[int]) == {"a": 1}


@pytest.mark.skipif(not is_py312_plus, reason="3.12+ syntax")
def test_simple_generic_roundtrip_dc(converter: BaseConverter):
    """PEP 695 dataclass generics work."""

    @dataclass
    class A[T]:
        a: T

    assert converter.structure({"a": "1"}, A[int]) == A(1)
    assert converter.unstructure(A(1)) == {"a": 1}

    if isinstance(converter, Converter):
        # Only supported on a Converter
        assert converter.unstructure(A(1), A[int]) == {"a": 1}


def test_type_aliases(converter: BaseConverter):
    """PEP 695 type aliases work."""
    type my_int = int

    assert converter.structure("42", my_int) == 42
    assert converter.unstructure(42, my_int) == 42

    type my_other_int = int

    # Manual hooks should work.

    converter.register_structure_hook_func(
        lambda t: t is my_other_int, lambda v, _: v + 10
    )
    converter.register_unstructure_hook_func(
        lambda t: t is my_other_int, lambda v: v - 20
    )

    assert converter.structure(1, my_other_int) == 11
    assert converter.unstructure(100, my_other_int) == 80


def test_type_aliases_simple_hooks(converter: BaseConverter):
    """PEP 695 type aliases work with `register_un/structure_hook`."""
    type my_other_int = int

    converter.register_structure_hook(my_other_int, lambda v, _: v + 10)
    converter.register_unstructure_hook(my_other_int, lambda v: v - 20)

    assert converter.structure(1, my_other_int) == 11
    assert converter.unstructure(100, my_other_int) == 80


def test_type_aliases_overwrite_base_hooks(converter: BaseConverter):
    """Overwriting base hooks should affect type aliases."""
    converter.register_structure_hook(int, lambda v, _: v + 10)
    converter.register_unstructure_hook(int, lambda v: v - 20)

    type my_int = int

    assert converter.structure(1, my_int) == 11
    assert converter.unstructure(100, my_int) == 80


def test_type_alias_with_children(converter: BaseConverter):
    """A type alias that chains to a hook that requires the type parameter works."""

    class TestClass:
        pass

    def structure_testclass(val, type):
        assert type is TestClass
        return TestClass

    converter.register_structure_hook(TestClass, structure_testclass)

    type TestAlias = TestClass
    assert converter.structure(None, TestAlias) is TestClass


def test_generic_type_alias(converter: BaseConverter):
    """Generic type aliases work.

    See https://docs.python.org/3/reference/compound_stmts.html#generic-type-aliases
    for details.
    """

    type Gen1[T] = T

    assert converter.structure("1", Gen1[int]) == 1

    type Gen2[K, V] = dict[K, V]

    assert converter.structure({"a": "1"}, Gen2[str, int]) == {"a": 1}


def test_recursive_generic_type_alias(converter: BaseConverter):
    """Recursive generic type aliases structure nested levels with the same arguments."""
    data = {
        "value": "1",
        "children": [{"value": "2", "children": [{"value": "3", "children": []}]}],
    }

    res = converter.structure(data, Tree[str])

    assert res == TreeNode("1", [TreeNode("2", [TreeNode("3", [])])])
    assert isinstance(res.children[0].children[0].value, str)


def test_recursive_generic_type_alias_dataclass(converter: BaseConverter):
    """Recursive generic type aliases work for dataclasses too."""
    data = {"value": "1", "children": [{"value": "2", "children": []}]}

    res = converter.structure(data, DataclassTree[str])

    assert res == TreeDataclass("1", [TreeDataclass("2", [])])
    assert isinstance(res.children[0].value, str)


def test_recursive_generic_type_alias_interleaved(converter: BaseConverter):
    """Different concrete recursive aliases can be structured interleaved.

    The hook prepared for one version must not be reused for another.
    """
    assert converter.get_structure_hook(Tree[str]) is not converter.get_structure_hook(
        Tree[int]
    )

    str_data = {"value": "1", "children": [{"value": "2", "children": []}]}
    int_data = {"value": 1, "children": [{"value": 2, "children": []}]}

    for _ in range(3):
        str_tree = converter.structure(str_data, Tree[str])
        int_tree = converter.structure(int_data, Tree[int])

    assert isinstance(str_tree.value, str)
    assert isinstance(str_tree.children[0].value, str)
    assert isinstance(int_tree.value, int)
    assert isinstance(int_tree.children[0].value, int)


def test_recursive_generic_type_alias_leaf_hooks(converter: BaseConverter):
    """Custom hooks for the leaf type apply per concrete type, recursively."""
    converter.register_structure_hook(int, lambda v, _: v * 1000)

    res = converter.structure(
        {"value": 1, "children": [{"value": 2, "children": []}]}, Tree[int]
    )

    assert res.value == 1000
    assert res.children[0].value == 2000


def test_recursive_generic_type_alias_roundtrip(genconverter: Converter):
    """Recursive generic type aliases unstructure back to their source data."""
    data = {"value": "1", "children": [{"value": "2", "children": []}]}

    node = genconverter.structure(data, Tree[str])

    assert genconverter.unstructure(node, Tree[str]) == data


def test_recursive_generic_type_alias_error_path():
    """Errors while structuring point to the offending leaf in the tree."""
    converter = Converter(detailed_validation=True)

    with pytest.raises(ClassValidationError) as exc_info:
        converter.structure(
            {"value": 1, "children": [{"value": "bad", "children": []}]}, Tree[int]
        )

    notes = []

    def collect(exc):
        notes.extend(getattr(exc, "__notes__", []))
        for sub in getattr(exc, "exceptions", []):
            collect(sub)

    collect(exc_info.value)
    # The path leads through `children` down to the invalid `value` leaf.
    assert any(getattr(note, "name", None) == "children" for note in notes)
    assert any(getattr(note, "name", None) == "value" for note in notes)


def test_self_referential_generic_alias_unsupported(converter: BaseConverter):
    """Directly self-referential generic aliases raise a clear error."""
    with pytest.raises(StructureHandlerNotFoundError, match=r"[Rr]ecursive"):
        converter.structure(["x", ["y"]], SelfReferential[str])
