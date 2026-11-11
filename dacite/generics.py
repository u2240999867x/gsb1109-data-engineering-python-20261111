# pylint: disable=unsupported-binary-operation
import sys
from dataclasses import Field, is_dataclass
from functools import reduce
from types import ModuleType, SimpleNamespace
from typing import Any, Dict, Generic, List, Mapping, Optional, Tuple, Type, TypeVar, get_type_hints

from dacite.exceptions import DaciteError, ForwardReferenceError

try:
    from typing import get_args, get_origin, Literal  # type: ignore
except ImportError:
    from typing_extensions import get_args, get_origin, Literal  # type: ignore


from .dataclasses import get_fields as dataclasses_get_fields


def __add_generics(type_origin: Any, type_args: Tuple, generics: Dict[TypeVar, Type]) -> None:
    """Adds (type var, concrete type) entries derived from a type's origin and args to the provided generics dict."""
    if type_origin and type_args and hasattr(type_origin, "__parameters__"):
        for param, arg in zip(type_origin.__parameters__, type_args):
            if isinstance(param, TypeVar):
                if param in generics and generics[param] != arg:
                    raise DaciteError(f"Ambiguous TypeVar: {generics[param]} != {arg}")
                generics[param] = arg


def __concretize(hint: Any, generics: Dict[TypeVar, Type]) -> Any:
    """Recursively replace type vars by concrete types."""

    if isinstance(hint, TypeVar):
        # Fall back on the original TypeVar if the generics dict does not contain it.
        # Setting config.check_types=False will in some cases still make from_dict work, albeit not type checked ofc.
        return generics.get(hint, hint)

    hint_origin = get_origin(hint)
    hint_args = get_args(hint)
    if hint_origin and hint_args and hint_origin is not Literal:
        concrete_hint_args = tuple(__concretize(a, generics) for a in hint_args)
        if concrete_hint_args != hint_args:
            if sys.version_info >= (3, 9):
                if sys.version_info >= (3, 10) and hint_origin is type(int | str):
                    # avoid "UnionType is not subscriptable"
                    return reduce(lambda a, b: a | b, concrete_hint_args)
                # construct a copy with concretized args
                return hint_origin[concrete_hint_args]
            # It's generally not a good practice to overwrite __args__,
            # and it even has become impossible starting from python 3.13 (read-only),
            # but changing the output of get_type_hints is harmless (see unit test)
            # and at least this way, we get it working for python 3.8.
            hint.__args__ = concrete_hint_args

    return hint


def __module_globals(data_class: Type) -> Dict[str, Any]:
    module: Optional[ModuleType] = sys.modules.get(data_class.__module__)
    return vars(module) if module is not None else {}


def __locate_unresolved_reference(
    data_class: Type, error: NameError, localns: Optional[Mapping[str, Any]]
) -> Tuple[str, str]:
    """Re-evaluate each field on its own to find which forward reference is missing and where."""
    module_globals = __module_globals(data_class)
    local_ns = dict(localns) if localns else {}
    reference_name = getattr(error, "name", None)
    for field in dataclasses_get_fields(data_class):
        probe = SimpleNamespace()
        probe.__annotations__ = {field.name: field.type}
        try:
            get_type_hints(probe, globalns=module_globals, localns=local_ns)
        except NameError as field_error:
            return field.name, getattr(field_error, "name", None) or reference_name or str(field_error)
    return "", reference_name or str(error)


def __resolve_type_hints(data_class: Type, localns: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Resolve a dataclass' annotations strictly within its module globals and the provided local context."""
    try:
        return get_type_hints(data_class, localns=dict(localns) if localns else None)
    except NameError as error:
        field_name, reference_name = __locate_unresolved_reference(data_class, error, localns)
        location = f' in field "{field_name}"' if field_name else ""
        raise ForwardReferenceError(f'"{reference_name}"{location}') from None


def orig(data_class: Type) -> Any:
    if is_dataclass(data_class):
        return data_class
    return get_origin(data_class)


def get_concrete_type_hints(
    data_class: Type, localns: Optional[Mapping[str, Any]] = None
) -> Dict[str, Any]:
    """
    An overwrite of typing.get_type_hints supporting generics and forward references,
    i.e. substituting concrete types in type vars and references.

    Forward references are resolved against ``localns`` (the per-plugin resolution
    context) together with the dataclass' own module globals only. Nothing is guessed
    by scanning other imported modules.
    """
    generics: Dict[TypeVar, Type] = {}

    dc_origin = get_origin(data_class)
    dc_args = get_args(data_class)
    __add_generics(dc_origin, dc_args, generics)

    if hasattr(data_class, "__orig_bases__"):
        for base in data_class.__orig_bases__:
            base_origin = get_origin(base)
            base_args = get_args(base)
            if base_origin is not Generic:
                __add_generics(base_origin, base_args, generics)

    data_class = orig(data_class)
    hints = __resolve_type_hints(data_class, localns)

    for key, hint in hints.copy().items():
        hints[key] = __concretize(hint, generics)

    return hints


def get_fields(data_class: Type) -> List[Field]:
    """An overwrite of dacite.dataclasses.get_fields supporting generics."""
    return dataclasses_get_fields(orig(data_class))
