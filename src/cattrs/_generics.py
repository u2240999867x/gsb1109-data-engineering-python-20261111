import sys
from collections.abc import Mapping
from typing import Any, ForwardRef, get_args, get_origin

from attrs import NOTHING
from typing_extensions import Self

from ._compat import copy_with, is_annotated, is_generic

__all__ = ["deep_copy_with", "resolve_forward_ref"]


def _globalns_for(owner: Any) -> dict[str, Any] | None:
    """Best-effort global namespace of the module `owner` is defined in."""
    owner = get_origin(owner) or owner
    module = sys.modules.get(getattr(owner, "__module__", None))
    return None if module is None else module.__dict__


def resolve_forward_ref(
    ref: str | ForwardRef, mapping: Mapping[str, Any], globalns: dict[str, Any] | None
) -> Any:
    """Resolve a stringified annotation or `ForwardRef` containing type parameters.

    `mapping` provides the bindings for the type parameters (for example the
    `T` in a PEP 695 ``type Tree[T] = ...`` alias), and `globalns` provides the
    module-level names (for example the `Tree` alias itself). If the reference
    cannot be resolved it is returned unchanged.
    """
    if globalns is None:
        return ref
    arg = ref if isinstance(ref, str) else ref.__forward_arg__
    try:
        if sys.version_info >= (3, 12):
            return ForwardRef(arg)._evaluate(
                globalns, dict(mapping), (), recursive_guard=frozenset()
            )
        return ForwardRef(arg)._evaluate(globalns, dict(mapping), frozenset())
    except Exception:
        return ref


def deep_copy_with(
    t,
    mapping: Mapping[str, Any],
    self_is=NOTHING,
    globalns: dict[str, Any] | None = None,
):
    if globalns is None and self_is is not NOTHING:
        globalns = _globalns_for(self_is)
    args = get_args(t)
    rest = ()
    if is_annotated(t) and args:
        # If we're dealing with `Annotated`, we only map the first type parameter
        rest = tuple(args[1:])
        args = (args[0],)
    new_args = (
        tuple(
            (
                self_is
                if a is Self and self_is is not NOTHING
                else (
                    mapping[a.__name__]
                    if hasattr(a, "__name__") and a.__name__ in mapping
                    else (
                        resolve_forward_ref(a, mapping, globalns)
                        if isinstance(a, (str, ForwardRef))
                        else (
                            deep_copy_with(a, mapping, self_is, globalns)
                            if is_generic(a)
                            else a
                        )
                    )
                )
            )
            for a in args
        )
        + rest
    )
    return copy_with(t, new_args) if new_args != args else t
