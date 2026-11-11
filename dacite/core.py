from dataclasses import is_dataclass
from itertools import zip_longest
from typing import TypeVar, Type, Optional, Mapping, Any, Collection, MutableMapping, List, cast

from dacite.cache import cache
from dacite.config import Config
from dacite.data import Data
from dacite.dataclasses import (
    get_default_value_for_field,
    DefaultValueNotFoundError,
    is_frozen,
)
from dacite.exceptions import (
    ForwardReferenceError,
    WrongTypeError,
    DaciteError,
    UnionMatchError,
    MissingValueError,
    DaciteFieldError,
    UnexpectedDataError,
    StrictUnionMatchError,
    DaciteAggregateError,
)
from dacite.types import (
    is_instance,
    is_generic_collection,
    is_union,
    extract_generic,
    is_optional,
    extract_origin_collection,
    is_init_var,
    extract_init_var,
    is_subclass,
)

from dacite.generics import get_concrete_type_hints, get_fields, orig

T = TypeVar("T")


def from_dict(data_class: Type[T], data: Data, config: Optional[Config] = None) -> T:
    """Create a data class instance from a dictionary.

    :param data_class: a data class type
    :param data: a dictionary of a input data
    :param config: a configuration of the creation process
    :return: an instance of a data class
    """
    return _build_dataclass(data_class=data_class, data=data, config=config or Config(), errors=None)


def from_dict_collecting_errors(
    data_class: Type[T], data: Data, config: Optional[Config] = None
) -> T:
    """Create a data class instance, collecting every independently detectable error.

    Unlike :func:`from_dict`, this does not stop at the first error. Missing values,
    wrong types, nested record errors, ambiguous union matches and unexpected keys are
    gathered into a single :class:`DaciteAggregateError`, each with a ``field_path``
    that locates it inside the original input. Constructors and ``__post_init__`` are
    only invoked once the whole input is prepared successfully; any exception they
    raise is propagated unchanged.
    """
    errors: List[DaciteFieldError] = []
    try:
        prepared = _build_dataclass(data_class=data_class, data=data, config=config or Config(), errors=errors)
    except _CollectionAborted:
        raise DaciteAggregateError(errors) from None
    return prepared.materialize()


def _build_dataclass(
    data_class: Type[T], data: Data, config: Config, errors: Optional[List[DaciteFieldError]]
) -> Any:
    init_values: MutableMapping[str, Any] = {}
    post_init_values: MutableMapping[str, Any] = {}

    try:
        data_class_hints = cache(get_concrete_type_hints)(data_class, localns=config.hashable_forward_references)
    except NameError as error:
        raise ForwardReferenceError(str(error)) from None
    data_class_fields = cache(get_fields)(data_class)

    if config.strict:
        extra_fields = set(data.keys()) - {f.name for f in data_class_fields}
        if extra_fields:
            unexpected_error = UnexpectedDataError(keys=extra_fields)
            if errors is None:
                raise unexpected_error
            errors.append(unexpected_error)

    for field in data_class_fields:
        field_type = data_class_hints[field.name]
        key = config.convert_key(field.name)

        if key in data:
            try:
                value = _build_value(type_=field_type, data=data[key], config=config, errors=errors)
            except _NestedErrors as nested:
                if errors is None:
                    raise
                for nested_error in nested.errors:
                    nested_error.update_path(field.name)
                    errors.append(nested_error)
                continue
            except DaciteFieldError as error:
                error.update_path(field.name)
                if errors is None:
                    raise
                errors.append(error)
                continue
            if config.check_types:
                checked_value = _type_check_shadow(value) if errors is not None else value
                if not is_instance(checked_value, field_type):
                    type_error = WrongTypeError(field_path=field.name, field_type=field_type, value=value)
                    if errors is None:
                        raise type_error
                    errors.append(type_error)
                    continue
        else:
            try:
                value = get_default_value_for_field(field, field_type)
            except DefaultValueNotFoundError:
                if not field.init:
                    continue
                missing_error = MissingValueError(field.name)
                if errors is None:
                    raise missing_error from None
                errors.append(missing_error)
                continue
        if field.init:
            init_values[field.name] = value
        elif not is_frozen(data_class):
            post_init_values[field.name] = value

    if errors is not None:
        # In collecting mode construction is deferred: no constructor or
        # __post_init__ runs while this record (or any nested one) has errors.
        if errors:
            raise _CollectionAborted()
        return _Deferred(data_class=data_class, init_values=init_values, post_init_values=post_init_values)

    instance = data_class(**init_values)

    for key, value in post_init_values.items():
        setattr(instance, key, value)

    return instance


class _CollectionAborted(Exception):
    """Internal signal that a record has collected errors and must not be constructed."""


class _NestedErrors(DaciteFieldError):
    """Carrier for errors detected in a nested record, never raised to the user."""

    def __init__(self, errors: List[DaciteFieldError]) -> None:
        super().__init__()
        self.errors = errors


class _Deferred:
    """A record whose constructor and __post_init__ have not run yet."""

    def __init__(
        self,
        data_class: Type,
        init_values: MutableMapping[str, Any],
        post_init_values: MutableMapping[str, Any],
    ) -> None:
        self.data_class = data_class
        self.init_values = init_values
        self.post_init_values = post_init_values

    def materialize(self) -> Any:
        instance = self.data_class(
            **{name: _materialize(value) for name, value in self.init_values.items()}
        )
        for name, value in self.post_init_values.items():
            setattr(instance, name, _materialize(value))
        return instance


_FAILED = object()


def _type_check_shadow(value: Any) -> Any:
    """Mirror a prepared value for ``is_instance`` checks without running constructors.

    Deferred records become bare instances (via ``object.__new__``) so that type checks
    see the real data class while constructors and ``__post_init__`` stay untouched.
    """
    if isinstance(value, _Deferred):
        shadow = object.__new__(orig(value.data_class))
        for name, item in value.init_values.items():
            object.__setattr__(shadow, name, _type_check_shadow(item))
        for name, item in value.post_init_values.items():
            object.__setattr__(shadow, name, _type_check_shadow(item))
        return shadow
    if isinstance(value, Mapping):
        return cast(Any, value.__class__)((key, _type_check_shadow(item)) for key, item in value.items())
    if isinstance(value, tuple):
        return value.__class__(_type_check_shadow(item) for item in value)
    if isinstance(value, (list, set, frozenset)):
        return value.__class__(_type_check_shadow(item) for item in value)
    return value


def _materialize(value: Any) -> Any:
    if isinstance(value, _Deferred):
        return value.materialize()
    if isinstance(value, Mapping):
        return cast(Any, value.__class__)((key, _materialize(item)) for key, item in value.items())
    if isinstance(value, tuple):
        return value.__class__(_materialize(item) for item in value)
    if isinstance(value, (list, set, frozenset)):
        return value.__class__(_materialize(item) for item in value)
    return value


def _build_value(type_: Type, data: Any, config: Config, errors: Optional[List[DaciteFieldError]]) -> Any:
    if is_init_var(type_):
        type_ = extract_init_var(type_)
    if type_ in config.type_hooks:
        data = config.type_hooks[type_](data)
    if is_optional(type_) and data is None:
        return data
    if is_union(type_):
        data = _build_value_for_union(union=type_, data=data, config=config, errors=errors)
    elif is_generic_collection(type_):
        data = _build_value_for_collection(collection=type_, data=data, config=config, errors=errors)
    elif cache(is_dataclass)(orig(type_)) and isinstance(data, Mapping):
        data = _build_nested_dataclass(data_class=type_, data=data, config=config, errors=errors)
    for cast_type in config.cast:
        if is_subclass(type_, cast_type):
            if is_generic_collection(type_):
                data = extract_origin_collection(type_)(data)
            else:
                data = type_(data)
            break
    return data


def _build_nested_dataclass(
    data_class: Type, data: Data, config: Config, errors: Optional[List[DaciteFieldError]]
) -> Any:
    if errors is None:
        return _build_dataclass(data_class=data_class, data=data, config=config, errors=None)
    nested_errors: List[DaciteFieldError] = []
    try:
        return _build_dataclass(data_class=data_class, data=data, config=config, errors=nested_errors)
    except _CollectionAborted:
        raise _NestedErrors(nested_errors) from None


def _build_value_for_union(
    union: Type, data: Any, config: Config, errors: Optional[List[DaciteFieldError]]
) -> Any:
    types = extract_generic(union)
    if is_optional(union) and len(types) == 2:
        return _build_value(type_=types[0], data=data, config=config, errors=errors)
    union_matches = {}
    for inner_type in types:
        # Speculative union branches must not leak their errors into the outer
        # collection: a failed alternative is simply not a match.
        branch_errors: Optional[List[DaciteFieldError]] = [] if errors is not None else None
        try:
            # noinspection PyBroadException
            try:
                value = _build_value(type_=inner_type, data=data, config=config, errors=branch_errors)
            except Exception:  # pylint: disable=broad-except
                continue
            if is_instance(_type_check_shadow(value) if errors is not None else value, inner_type):
                if config.strict_unions_match:
                    union_matches[inner_type] = value
                else:
                    return value
        except DaciteError:
            pass
    if config.strict_unions_match and union_matches:
        if len(union_matches) > 1:
            raise StrictUnionMatchError(union_matches)
        return union_matches.popitem()[1]
    if not config.check_types:
        return data
    raise UnionMatchError(field_type=union, value=data)


def _build_value_for_collection(
    collection: Type, data: Any, config: Config, errors: Optional[List[DaciteFieldError]]
) -> Any:
    data_type = data.__class__
    item_errors: Optional[List[DaciteFieldError]] = None
    if errors is not None:
        # Each collection owns its errors so they bubble up as a single carrier and
        # the enclosing field can prefix its name, e.g. "items[0].i".
        item_errors = []
    if isinstance(data, Mapping) and is_subclass(collection, Mapping):
        item_type = extract_generic(collection, defaults=(Any, Any))[1]
        result = data_type(
            (key, _build_collection_item(type_=item_type, data=value, config=config, errors=item_errors, index=key))
            for key, value in data.items()
        )
    elif is_subclass(collection, tuple):
        if not data:
            return data_type()
        types = extract_generic(collection)
        if len(types) == 2 and types[1] == Ellipsis:
            result = data_type(
                _build_collection_item(type_=types[0], data=item, config=config, errors=item_errors, index=index)
                for index, item in enumerate(data)
            )
        else:
            result = data_type(
                _build_collection_item(type_=type_, data=item, config=config, errors=item_errors, index=index)
                for index, (item, type_) in enumerate(zip_longest(data, types))
            )
    elif isinstance(data, Collection) and is_subclass(collection, Collection):
        item_type = extract_generic(collection, defaults=(Any,))[0]
        result = data_type(
            _build_collection_item(type_=item_type, data=item, config=config, errors=item_errors, index=index)
            for index, item in enumerate(data)
        )
    else:
        return data
    if item_errors:
        raise _NestedErrors(item_errors)
    return result


def _build_collection_item(
    type_: Type, data: Any, config: Config, errors: Optional[List[DaciteFieldError]], index: Any
) -> Any:
    if errors is None:
        return _build_value(type_=type_, data=data, config=config, errors=None)
    try:
        value = _build_value(type_=type_, data=data, config=config, errors=errors)
    except _NestedErrors as nested:
        for nested_error in nested.errors:
            nested_error.prepend_index(index)
            errors.append(nested_error)
        return _FAILED
    except DaciteFieldError as error:
        error.prepend_index(index)
        errors.append(error)
        return _FAILED
    if value is not _FAILED and type_ is not None and config.check_types:
        checked_value = _type_check_shadow(value)
        if not is_instance(checked_value, type_):
            type_error = WrongTypeError(field_type=type_, value=value)
            type_error.prepend_index(index)
            errors.append(type_error)
            return _FAILED
    return value
