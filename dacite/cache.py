from functools import lru_cache, wraps
from typing import Any, Callable, Optional, TypeVar

T = TypeVar("T", bound=Callable)

__MAX_SIZE: Optional[int] = 2048


@lru_cache(maxsize=None)
def cache(function: T) -> T:
    lru_cached = lru_cache(maxsize=get_cache_size(), typed=True)(function)  # type: ignore

    @wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return lru_cached(*args, **kwargs)
        except TypeError as error:
            # Unhashable arguments (e.g. typing.Annotated aliases carrying unhashable
            # metadata, or generics nesting such an alias) cannot be cached keys.
            # Fall back to a direct call instead of letting the lookup fail. A
            # TypeError raised by the wrapped function itself is re-raised unchanged.
            if _has_unhashable_argument(args, kwargs):
                return function(*args, **kwargs)
            raise error

    # Keep the lru_cache introspection API available on the returned wrapper.
    wrapper.cache_info = lru_cached.cache_info  # type: ignore[attr-defined]
    wrapper.cache_clear = lru_cached.cache_clear  # type: ignore[attr-defined]
    return wrapper  # type: ignore


def _has_unhashable_argument(args: tuple, kwargs: dict) -> bool:
    try:
        for value in args:
            hash(value)
        for key, value in kwargs.items():
            hash(key)
            hash(value)
    except TypeError:
        return True
    return False


def set_cache_size(size: Optional[int]) -> None:
    global __MAX_SIZE  # pylint: disable=global-statement
    __MAX_SIZE = size


def get_cache_size() -> Optional[int]:
    global __MAX_SIZE  # pylint: disable=global-variable-not-assigned
    return __MAX_SIZE


def clear_cache() -> None:
    cache.cache_clear()
