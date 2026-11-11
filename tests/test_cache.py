from unittest.mock import Mock, call

from dacite.cache import set_cache_size, get_cache_size, cache


def test_cache_size():
    set_cache_size(4321)
    assert get_cache_size() == 4321

    function = Mock()

    cache(function)(get_cache_size())
    set_cache_size(8765)
    cache(function)(get_cache_size())

    set_cache_size(None)
    cache(function)(get_cache_size())

    assert function.call_count == 3
    assert function.mock_calls == [call(4321), call(8765), call(None)]


def test_cache_from_function():
    function = Mock()

    cache(function)()
    cache(function)()

    function.assert_called_once()


def test_cache_bypasses_unhashable_arguments():
    function = Mock(side_effect=lambda value: value)

    cached = cache(function)
    unhashable = {"key": "value"}

    assert cached(unhashable) is unhashable
    assert cached(unhashable) is unhashable

    assert function.call_count == 2
    assert cached.cache_info().hits == 0


def test_cache_keeps_introspection_api():
    cached = cache(Mock())

    assert hasattr(cached, "cache_info")
    assert hasattr(cached, "cache_clear")
