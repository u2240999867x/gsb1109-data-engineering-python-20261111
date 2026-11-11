"""Strategy for open (forward-compatible) enums."""

from enum import Enum

from .. import BaseConverter
from ..enums import (
    UnknownEnumValue,
    open_enum_structure_factory,
    open_enum_unstructure_factory,
)

__all__ = ["use_open_enums"]


def use_open_enums(converter: BaseConverter, *enums: type[Enum]) -> None:
    """Configure the converter to structure the given enums openly.

    Known values are structured into their enum members as usual. Values with
    no matching member (for example, produced by a newer version of a service)
    are preserved as :class:`cattrs.enums.UnknownEnumValue`, keeping both the
    original value and the target enum, instead of raising or being coerced
    into a default member. Unstructuring an :class:`UnknownEnumValue` yields
    the original value, so unknown states round-trip losslessly.

    An :class:`UnknownEnumValue` can be explicitly reinterpreted by
    structuring it again once the enum has gained the corresponding member.

    This does not change the default, strict enum handling; enums not passed
    here still reject unknown values.

    :param converter: The converter to apply the strategy to.
    :param enums: The enums to structure openly.

    .. versionadded:: 26.3.0
    """
    for enum in enums:
        if not (isinstance(enum, type) and issubclass(enum, Enum)):
            raise TypeError(f"{enum!r} is not an Enum subclass")
        converter.register_structure_hook(
            enum, open_enum_structure_factory(enum, converter)
        )
        converter.register_unstructure_hook(
            enum, open_enum_unstructure_factory(enum, converter)
        )
    converter.register_unstructure_hook(UnknownEnumValue, lambda v: v.value)
