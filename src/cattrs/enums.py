from collections.abc import Callable
from enum import Enum
from typing import TYPE_CHECKING, Any, Generic, TypeVar

if TYPE_CHECKING:
    from .converters import BaseConverter

__all__ = [
    "UnknownEnumValue",
    "enum_structure_factory",
    "enum_unstructure_factory",
    "open_enum_structure_factory",
    "open_enum_unstructure_factory",
]

E = TypeVar("E", bound=Enum)


class UnknownEnumValue(Generic[E]):
    """An enum value not (yet) known to this version of the model.

    Produced by :func:`open_enum_structure_factory` (usually via
    :func:`cattrs.strategies.use_open_enums`) when a payload contains a value
    with no corresponding member on the target enum. The original value and
    the target enum are preserved so the payload can be round-tripped
    losslessly, and so the value can be explicitly reinterpreted once the
    model gains a matching member.

    This is deliberately *not* an enum member; it does not compare equal to
    any member of the enum and cannot be used as a stand-in for one.

    .. versionadded:: 26.3.0
    """

    __slots__ = ("enum", "value")

    def __init__(self, enum: type[E], value: Any) -> None:
        self.enum = enum
        self.value = value

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, UnknownEnumValue):
            return NotImplemented
        return self.enum is other.enum and self.value == other.value

    def __hash__(self) -> int:
        return hash((self.enum, self.value))

    def __repr__(self) -> str:
        return f"UnknownEnumValue({self.enum.__name__}, {self.value!r})"


def enum_unstructure_factory(
    type: type[Enum], converter: "BaseConverter"
) -> Callable[[Enum], Any]:
    """A factory for generating enum unstructure hooks.

    If the enum is a typed enum (has `_value_`), we use the underlying value's hook.
    Otherwise, we use the value directly.
    """
    if "_value_" in type.__annotations__:
        return lambda e: converter.unstructure(e.value)

    return lambda e: e.value


def enum_structure_factory(
    type: type[Enum], converter: "BaseConverter"
) -> Callable[[Any, type[Enum]], Enum]:
    """A factory for generating enum structure hooks.

    If the enum is a typed enum (has `_value_`), we structure the value first.
    Otherwise, we use the value directly.
    """
    if "_value_" in type.__annotations__:
        val_type = type.__annotations__["_value_"]
        val_hook = converter.get_structure_hook(val_type)
        return lambda v, _: type(val_hook(v, val_type))

    return lambda v, _: type(v)


def open_enum_structure_factory(
    type: type[E], converter: "BaseConverter"
) -> Callable[[Any, type[E]], "E | UnknownEnumValue[E]"]:
    """A factory for generating open enum structure hooks.

    Like :func:`enum_structure_factory`, but values with no matching member
    are preserved as :class:`UnknownEnumValue` instead of raising, keeping
    both the original value and the target enum.

    An :class:`UnknownEnumValue` may be structured again to explicitly
    reinterpret it, for example after the enum gains the missing member.

    .. versionadded:: 26.3.0
    """
    strict_hook = enum_structure_factory(type, converter)

    def structure_open_enum(v: Any, _: Any) -> "E | UnknownEnumValue[E]":
        if isinstance(v, UnknownEnumValue):
            if v.enum is not type:
                raise ValueError(f"{v!r} cannot be structured as {type!r}")
            v = v.value
        try:
            return strict_hook(v, type)
        except Exception:
            return UnknownEnumValue(type, v)

    return structure_open_enum


def open_enum_unstructure_factory(
    type: type[Enum], converter: "BaseConverter"
) -> Callable[["Enum | UnknownEnumValue"], Any]:
    """A factory for generating open enum unstructure hooks.

    Members are unstructured like :func:`enum_unstructure_factory`;
    :class:`UnknownEnumValue` instances are unstructured to their original
    value, as-is.

    .. versionadded:: 26.3.0
    """
    member_hook = enum_unstructure_factory(type, converter)

    def unstructure_open_enum(e: "Enum | UnknownEnumValue") -> Any:
        if isinstance(e, UnknownEnumValue):
            return e.value
        return member_hook(e)

    return unstructure_open_enum
