from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from attrs import NOTHING, Attribute, Factory

from .._compat import get_args, is_annotated, is_bare_final
from ..dispatch import StructureHook
from ..errors import StructureHandlerNotFoundError
from ..fns import raise_error
from ._consts import AttributeOverride, Extras

if TYPE_CHECKING:
    from ..converters import BaseConverter

#: Class attribute holding an `Extras` declaration.
EXTRAS_ATTR = "__cattrs_extras__"


def resolve_extras(cl: Any, attrs: list[Attribute]) -> Extras | None:
    """Resolve an `Extras` declaration attached to ``cl``.

    Return `None` if the class does not opt in. Raise `ValueError` if the
    declared target attribute does not exist on the class.
    """
    extras = getattr(cl, EXTRAS_ATTR, None)
    if extras is None:
        return None
    if not isinstance(extras, Extras):
        raise TypeError(
            f"{EXTRAS_ATTR} on {cl!r} must be an Extras instance, got {extras!r}."
        )
    target = extras.target
    target_attr = next((a for a in attrs if a.name == target), None)
    if target_attr is None:
        raise ValueError(
            f"Extras target {target!r} is not an attribute of {cl.__qualname__}."
        )
    if extras.field is not None and target_attr.init and target_attr.default is NOTHING:
        raise ValueError(
            f"Extras target {target!r} of {cl.__qualname__} must have a default "
            f"or be init=False, since retained fields are written after "
            f"instantiation."
        )
    return extras


def collect_field_extras(
    o: Mapping[str, Any], known_keys: frozenset[str], target_key: str
) -> dict[str, Any]:
    """Collect unknown input keys for ``Extras(field=...)``.

    A payload explicitly using the retention attribute name is accepted as a
    mapping seed; other unknown keys are merged on top of it.
    """
    extras = {k: v for k, v in o.items() if k not in known_keys or k == target_key}
    seed = extras.pop(target_key, NOTHING)
    if seed is NOTHING:
        return extras
    if not isinstance(seed, Mapping):
        raise TypeError(
            f"Reserved extra-fields field {target_key!r} must contain a mapping, "
            f"got {type(seed).__name__}."
        )
    merged = dict(seed)
    merged.update(extras)
    return merged


def collect_key_extras(o: Mapping[str, Any], reserved_key: str) -> dict[str, Any]:
    """Read the reserved nested mapping for ``Extras(key=...)``."""
    if reserved_key not in o:
        return {}
    value = o[reserved_key]
    if not isinstance(value, Mapping):
        raise TypeError(
            f"Reserved extra-fields key {reserved_key!r} must contain a mapping, "
            f"got {type(value).__name__}."
        )
    return dict(value)


def merge_extras(
    res: dict[str, Any], extras: Mapping[str, Any], cl: Any
) -> dict[str, Any]:
    """Merge retained extras back at the top level on unstructuring.

    Declared field keys always win; a retained key colliding with one is a
    `ValueError` instead of being silently overwritten.
    """
    if not extras:
        return res
    collisions = extras.keys() & res.keys()
    if collisions:
        raise ValueError(
            f"Retained extra field(s) {sorted(collisions)} collide with declared "
            f"field key(s) while unstructuring {cl.__qualname__}."
        )
    res.update(extras)
    return res


def set_retained_extras(
    instance: Any, target: str, retained: dict[str, Any], cl: Any
) -> None:
    """Write the retained extras mapping onto ``instance``.

    Frozen or read-only targets produce a clear error.
    """
    try:
        setattr(instance, target, retained)
    except AttributeError:
        raise AttributeError(
            f"Cannot write retained extra fields onto frozen or read-only "
            f"attribute {target!r} of {cl.__qualname__}."
        ) from None


def _annotated_override_or_default(
    type: Any, default: AttributeOverride
) -> AttributeOverride:
    """
    If the type is Annotated containing an AttributeOverride, return it.
    Otherwise, return the default.
    """
    if is_annotated(type):
        for arg in get_args(type):
            if isinstance(arg, AttributeOverride):
                return arg

    return default


def find_structure_handler(
    a: Attribute, type: Any, c: BaseConverter, prefer_attrs_converters: bool = False
) -> StructureHook | None:
    """Find the appropriate structure handler to use.

    Return `None` if no handler should be used.
    """
    try:
        if a.converter is not None and prefer_attrs_converters:
            # If the user as requested to use attrib converters, use nothing
            # so it falls back to that.
            handler = None
        elif (
            a.converter is not None and not prefer_attrs_converters and type is not None
        ):
            try:
                handler = c.get_structure_hook(type, cache_result=False)
            except StructureHandlerNotFoundError:
                handler = None
            else:
                # The legacy way, should still work.
                if handler == raise_error:
                    handler = None
        elif type is not None:
            if (
                is_bare_final(type)
                and a.default is not NOTHING
                and not isinstance(a.default, Factory)
            ):
                # This is a special case where we can use the
                # type of the default to dispatch on.
                type = a.default.__class__
                handler = c.get_structure_hook(type, cache_result=False)
                if handler == c._structure_call:
                    # Finals can't really be used with _structure_call, so
                    # we wrap it so the rest of the toolchain doesn't get
                    # confused.

                    def handler(v, _, _h=handler):
                        return _h(v, type)

            else:
                handler = c.get_structure_hook(type, cache_result=False)
        else:
            handler = c.structure
        return handler
    except RecursionError:
        # This means we're dealing with a reference cycle, so use late binding.
        return c.structure
