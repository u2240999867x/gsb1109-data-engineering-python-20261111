"""Utilities for type aliases."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any, ForwardRef, get_args, get_origin

from ._compat import is_generic
from ._generics import deep_copy_with
from .dispatch import StructureHook, UnstructureHook
from .errors import StructureHandlerNotFoundError
from .gen._generics import generate_mapping

if TYPE_CHECKING:
    from .converters import BaseConverter

__all__ = [
    "get_type_alias_base",
    "is_type_alias",
    "type_alias_structure_factory",
    "type_alias_unstructure_factory",
]

if sys.version_info >= (3, 12):
    from types import GenericAlias
    from typing import TypeAliasType

    def is_type_alias(type: Any) -> bool:
        """Is this a PEP 695 type alias?"""
        return isinstance(
            type.__origin__ if type.__class__ is GenericAlias else type, TypeAliasType
        )

else:

    def is_type_alias(type: Any) -> bool:
        """Is this a PEP 695 type alias?"""
        return False


def get_type_alias_base(type: Any) -> Any:
    """
    What is this a type alias of?

    Works only on 3.12+.
    """
    return type.__value__


def _contains_self_reference(t: Any, alias: Any) -> bool:
    """Does the type expression `t` reference the type alias `alias`?"""
    if t is alias or get_origin(t) is alias:
        return True
    if isinstance(t, (str, ForwardRef)):
        arg = t if isinstance(t, str) else t.__forward_arg__
        return arg.split("[")[0].strip() == getattr(alias, "__name__", None)
    return any(_contains_self_reference(a, alias) for a in get_args(t))


def _resolve_alias_base(type: Any) -> tuple[Any, Any]:
    """Resolve a (possibly generic) type alias to its substituted base type.

    Returns the base type and the alias itself. Forward references inside the
    alias value (for example recursive references) are resolved against the
    alias's module and the alias's own type parameters.
    """
    base = get_type_alias_base(type)
    alias = get_origin(type) or type
    module = sys.modules.get(getattr(alias, "__module__", None))
    globalns = None if module is None else module.__dict__
    if is_generic(type):
        mapping = generate_mapping(type)
        if base.__name__ in mapping:
            # Probably just type T = T
            base = mapping[base.__name__]
        else:
            base = deep_copy_with(base, mapping, globalns=globalns)
    elif is_generic(base):
        # A non-generic alias whose value is generic (for example
        # `type X = list["X"]`); resolve any forward references.
        base = deep_copy_with(base, {}, globalns=globalns)
    return base, alias


def type_alias_structure_factory(type: Any, converter: BaseConverter) -> StructureHook:
    base, alias = _resolve_alias_base(type)
    try:
        res = converter.get_structure_hook(base)
    except StructureHandlerNotFoundError:
        if _contains_self_reference(base, alias):
            raise StructureHandlerNotFoundError(
                f"Unsupported recursive type alias: {alias.__name__!r}. "
                "Directly self-referential generic type aliases (for example "
                "`type Tree[T] = T | list[Tree[T]]`) are not supported; define a "
                "recursive attrs class or dataclass and alias that instead.",
                type_=type,
            ) from None
        raise
    if res == converter._structure_call:
        # we need to replace the type arg of `structure_call`
        return lambda v, _, __base=base: __base(v)
    return lambda v, _, __base=base: res(v, __base)


def type_alias_unstructure_factory(
    type: Any, converter: BaseConverter
) -> UnstructureHook:
    base, _ = _resolve_alias_base(type)
    return converter.get_unstructure_hook(base)
