import string
import sys
from abc import abstractmethod, ABCMeta

import pytest

from boltons.cacheutils import LRU, LRI, cached, cachedmethod, cachedproperty, MinIDMap, ThresholdCounter
from boltons.cacheutils import (SignatureCacheKeyBuilder,
                                UncacheableArgumentError,
                                make_cache_key,
                                make_signature_cache_key)


class CountingCallable:
    def __init__(self):
        self.call_count = 0

    def __call__(self, *a, **kw):
        self.call_count += 1
        return self.call_count


def test_lru_add():
    cache = LRU(max_size=3)
    for i in range(4):
        cache[i] = i
    assert len(cache) == 3
    assert 0 not in cache


def test_lri():
    cache_size = 10
    bc = LRI(cache_size, on_miss=lambda k: k.upper())
    for idx, char in enumerate(string.ascii_letters):
        x = bc[char]
        assert x == char.upper()
        least_recent_insert_index = idx - cache_size
        if least_recent_insert_index >= 0:
            # least recently inserted object evicted
            assert len(bc) == cache_size
            for char in string.ascii_letters[least_recent_insert_index+1:idx]:
                assert char in bc

    # test that reinserting an existing key changes eviction behavior
    bc[string.ascii_letters[-cache_size+1]] = "new value"
    least_recently_inserted_key = string.ascii_letters[-cache_size+2]
    bc["unreferenced_key"] = "value"
    keys_in_cache = [
        string.ascii_letters[i]
        for i in range(-cache_size + 1, 0)
        if string.ascii_letters[i] != least_recently_inserted_key
    ]
    keys_in_cache.append("unreferenced_key")
    assert len(bc) == cache_size
    for k in keys_in_cache:
        assert k in bc


def test_lri_cache_eviction():
    """
    Regression test
    Original LRI implementation had a bug where the specified cache
    size only supported `max_size` number of inserts to the cache,
    rather than support `max_size` number of keys in the cache. This
    would result in some unintuitive behavior, where a key is evicted
    recently inserted value would be evicted from the cache if the key
    inserted was inserted `max_size` keys earlier.
    """
    test_cache = LRI(2)
    # dequeue: (key1); dict keys: (key1)
    test_cache["key1"] = "value1"
    # dequeue: (key1, key1); dict keys: (key1)
    test_cache["key1"] = "value1"
    # dequeue: (key1, key1, key2); dict keys: (key1, key2)
    test_cache["key2"] = "value2"
    # dequeue: (key1, key2, key3); dict keys: (key2, key3)
    test_cache["key3"] = "value3"
    # will error here since we evict key1 from the cache and it doesn't
    # exist in the dict anymore
    test_cache["key3"] = "value3"


def test_cache_sizes_on_repeat_insertions():
    """
    Regression test
    Original LRI implementation had an unbounded size of memory
    regardless of the value for its `max_size` parameter due to a naive
    insertion algorithm onto an underlying deque data structure. To
    prevent memory leaks, this test will assert that a cache does not
    grow past its max size given values of a uniform memory footprint
    """
    caches_to_test = (LRU, LRI)
    for cache_type in caches_to_test:
        test_cache = cache_type(2)
        # note strings are used to force allocation of memory
        test_cache["key1"] = "1"
        test_cache["key2"] = "1"
        initial_list_size = len(test_cache._get_flattened_ll())
        for k in test_cache:
            for __ in range(100):
                test_cache[k] = "1"
        list_size_after_inserts = len(test_cache._get_flattened_ll())
        assert initial_list_size == list_size_after_inserts


def test_lru_basic():
    lru = LRU(max_size=1)
    repr(lru)                   # sanity

    lru['hi'] = 0
    lru['bye'] = 1
    assert len(lru) == 1
    lru['bye']
    assert lru.get('hi') is None

    del lru['bye']
    assert 'bye' not in lru
    assert len(lru) == 0
    assert not lru

    try:
        lru.pop('bye')
    except KeyError:
        pass
    else:
        assert False

    default = object()
    assert lru.pop('bye', default) is default

    try:
        lru.popitem()
    except KeyError:
        pass
    else:
        assert False

    lru['another'] = 1
    assert lru.popitem() == ('another', 1)

    lru['yet_another'] = 2
    assert lru.pop('yet_another') == 2

    lru['yet_another'] = 3
    assert lru.pop('yet_another', default) == 3

    lru['yet_another'] = 4
    lru.clear()
    assert not lru

    lru['yet_another'] = 5
    second_lru = LRU(max_size=1)
    assert lru.copy() == lru

    second_lru['yet_another'] = 5
    assert second_lru == lru
    assert lru == second_lru

    lru.update(LRU(max_size=2, values=[('a', 1),
                                       ('b', 2)]))
    assert len(lru) == 1
    assert 'yet_another' not in lru

    lru.setdefault('x', 2)
    assert dict(lru) == {'x': 2}
    lru.setdefault('x', 3)
    assert dict(lru) == {'x': 2}

    assert lru != second_lru
    assert second_lru != lru


@pytest.mark.parametrize("lru_class", [LRU, LRI])
def test_lru_dict_replacement(lru_class):
    # see issue #348
    cache = lru_class()

    # Add an entry.
    cache['a'] = 1

    # Normal __getitem__ access.
    assert cache['a'] == 1  # passes.
    # Convert to dict.
    assert dict(cache) == {'a': 1}  # passes.
    # Another way to access the only value.
    assert list(cache.values())[0] == 1  # passes.

    # Replace the existing 'a' entry with a new value.
    cache['a'] = 200

    # __getitem__ works as expected.
    assert cache['a'] == 200  # passes.

    # Both dict and accessing via values() return the old entry: 1.
    assert dict(cache) == {'a': 200}  # fails.
    assert list(cache.values())[0] == 200


def test_lru_with_dupes():
    SIZE = 2
    lru = LRU(max_size=SIZE)
    for i in [0, 0, 1, 1, 2, 2]:
        lru[i] = i
        assert _test_linkage(lru._anchor, SIZE + 1), 'linked list invalid'


def test_lru_with_dupes_2():
    "From Issue #55, h/t github.com/mt"
    SIZE = 3
    lru = LRU(max_size=SIZE)
    keys = ['A', 'A', 'B', 'A', 'C', 'B', 'D', 'E']
    for i, k in enumerate(keys):
        lru[k] = 'HIT'
        assert _test_linkage(lru._anchor, SIZE + 1), 'linked list invalid'

    return


def _test_linkage(dll, max_count=10000, prev_idx=0, next_idx=1):
    """A function to test basic invariants of doubly-linked lists (with
    links made of Python lists).

    1. Test that the list is not longer than a certain length
    2. That the forward links (indicated by `next_idx`) correspond to
    the backward links (indicated by `prev_idx`).

    The `dll` parameter is the root/anchor link of the list.
    """
    start = cur = dll
    i = 0
    prev = None
    while 1:
        if i > max_count:
            raise Exception("did not return to anchor link after %r rounds"
                            % max_count)
        if prev is not None and cur is start:
            break
        prev = cur
        cur = cur[next_idx]
        if cur[prev_idx] is not prev:
            raise Exception('prev_idx does not point to prev at i = %r' % i)
        i += 1

    return True


def test_cached_dec():
    lru = LRU()
    inner_func = CountingCallable()
    func = cached(lru)(inner_func)

    assert inner_func.call_count == 0
    func()
    assert inner_func.call_count == 1
    func()
    assert inner_func.call_count == 1
    func('man door hand hook car door')
    assert inner_func.call_count == 2

    return


def test_unscoped_cached_dec():
    lru = LRU()
    inner_func = CountingCallable()
    func = cached(lru, scoped=False)(inner_func)

    other_inner_func = CountingCallable()
    other_func = cached(lru, scoped=False)(other_inner_func)

    assert inner_func.call_count == 0
    func('a')
    assert inner_func.call_count == 1
    func('a')

    other_func('a')
    assert other_inner_func.call_count == 0
    return


def test_scoped_cached_dec():
    lru = LRU()
    inner_func = CountingCallable()
    func = cached(lru)(inner_func)

    other_inner_func = CountingCallable()
    other_func = cached(lru)(other_inner_func)

    func('a')
    func('a')
    other_func('a')
    other_func('a')
    assert inner_func.call_count == 1
    assert other_inner_func.call_count == 1
    assert len(lru) == 2


def test_callable_cached_dec():
    lru = LRU()
    get_lru = lambda: lru

    inner_func = CountingCallable()
    func = cached(get_lru)(inner_func)

    assert inner_func.call_count == 0
    func()
    assert inner_func.call_count == 1
    func()
    assert inner_func.call_count == 1

    lru.clear()

    func()
    assert inner_func.call_count == 2
    func()
    assert inner_func.call_count == 2

    print(repr(func))

    return


def test_cachedmethod():
    class Car:
        def __init__(self, cache=None):
            self.h_cache = LRI() if cache is None else cache
            self.door_count = 0
            self.hook_count = 0
            self.hand_count = 0

        @cachedmethod('h_cache')
        def hand(self, *a, **kw):
            self.hand_count += 1

        @cachedmethod(lambda obj: obj.h_cache)
        def hook(self, *a, **kw):
            self.hook_count += 1

        @cachedmethod('h_cache', scoped=False)
        def door(self, *a, **kw):
            self.door_count += 1

    car = Car()

    # attribute name-style
    assert car.hand_count == 0
    car.hand('h', a='nd')
    assert car.hand_count == 1
    car.hand('h', a='nd')
    assert car.hand_count == 1

    # callable-style
    assert car.hook_count == 0
    car.hook()
    assert car.hook_count == 1
    car.hook()
    assert car.hook_count == 1

    # Ensure that non-selfish caches share the cache nicely
    lru = LRU()
    car_one = Car(cache=lru)
    assert car_one.door_count == 0
    car_one.door('bob')
    assert car_one.door_count == 1
    car_one.door('bob')
    assert car_one.door_count == 1

    car_two = Car(cache=lru)
    assert car_two.door_count == 0
    car_two.door('bob')
    assert car_two.door_count == 0

    # try unbound for kicks
    Car.door(Car(), 'bob')

    # always check the repr
    print(repr(car_two.door))
    print(repr(Car.door))
    return


def test_cachedmethod_maintains_func_abstraction():
    ABC = ABCMeta('ABC', (object,), {})

    class Car(ABC):

        def __init__(self, cache=None):
            self.h_cache = LRI() if cache is None else cache
            self.hand_count = 0

        @cachedmethod('h_cache')
        @abstractmethod
        def hand(self, *a, **kw):
            self.hand_count += 1

    with pytest.raises(TypeError):
        Car()


def test_cachedproperty():
    class Proper:
        def __init__(self):
            self.expensive_func = CountingCallable()

        @cachedproperty
        def useful_attr(self):
            """Useful DocString"""
            return self.expensive_func()

    prop = Proper()

    assert prop.expensive_func.call_count == 0
    assert prop.useful_attr == 1
    assert prop.expensive_func.call_count == 1
    assert prop.useful_attr == 1
    assert prop.expensive_func.call_count == 1

    # Make sure original DocString is accessible
    assert Proper.useful_attr.__doc__ == "Useful DocString"

    prop.useful_attr += 1  # would not be possible with normal properties
    assert prop.useful_attr == 2

    delattr(prop, 'useful_attr')
    assert prop.expensive_func.call_count == 1
    assert prop.useful_attr
    assert prop.expensive_func.call_count == 2

    repr(Proper.useful_attr)


def test_cachedproperty_maintains_func_abstraction():
    ABC = ABCMeta('ABC', (object,), {})

    class AbstractExpensiveCalculator(ABC):

        @cachedproperty
        @abstractmethod
        def calculate(self):
            pass

    with pytest.raises(TypeError):
        AbstractExpensiveCalculator()


def test_min_id_map():
    import sys
    if '__pypy__' in sys.builtin_module_names:
        return  # TODO: pypy still needs some work

    midm = MinIDMap()

    class Foo:
        def __init__(self, val):
            self.val = val

    # use this circular array to have them periodically collected
    ref_wheel = [None, None, None]

    for i in range(1000):
        nxt = Foo(i)
        ref_wheel[i % len(ref_wheel)] = nxt
        assert midm.get(nxt) <= len(ref_wheel)
        if i % 10 == 0:
            midm.drop(nxt)

    # test __iter__
    assert sorted([f.val for f in list(midm)[:10]]) == list(range(1000 - len(ref_wheel), 1000))

    items = list(midm.iteritems())
    assert isinstance(items[0][0], Foo)
    assert sorted(item[1] for item in items) == list(range(0, len(ref_wheel)))


def test_threshold_counter():
    tc = ThresholdCounter(threshold=0.1)
    tc.add(1)

    assert tc.items() == [(1, 1)]

    tc.update([2] * 10)

    assert tc.get(1) == 0

    tc.add(5)
    assert 5 in tc

    assert len(list(tc.elements())) == 11

    assert tc.threshold == 0.1
    assert tc.get_common_count() == 11
    assert tc.get_uncommon_count() == 1  # bc the initial 1 was dropped
    assert round(tc.get_commonality(), 2) == 0.92
    assert tc.most_common(2) == [(2, 10), (5, 1)]
    assert tc.most_common() == [(2, 10), (5, 1)]
    assert tc.most_common(0) == []
    assert list(tc.elements()) == ([2] * 10) + [5]

    assert tc[2] == 10
    assert len(tc) == 2
    assert sorted(tc.keys()) == [2, 5]
    assert sorted(tc.values()) == [1, 10]
    assert sorted(tc.items()) == [(2, 10), (5, 1)]


def test_threshold_counter_update_mapping_counts():
    tc = ThresholdCounter()
    tc.add('a')
    tc.update({'a': 3, 'b': 2, 'zero': 0})
    assert dict(tc.items()) == {'a': 4, 'b': 2}
    assert tc.total == 6


def test_threshold_counter_update_keyword_counts():
    tc = ThresholdCounter()
    tc.update(['a'], a=3, b=2, zero=0)
    assert dict(tc.items()) == {'a': 4, 'b': 2}
    assert tc.total == 6


def test_threshold_counter_update_mapping_compaction():
    tc = ThresholdCounter(threshold=0.1)
    expected = ThresholdCounter(threshold=0.1)
    tc.add('rare')
    expected.add('rare')
    tc.update({'a': 10, 'b': 5})
    expected.update(['a'] * 10 + ['b'] * 5)
    assert dict(tc.items()) == dict(expected.items())
    assert tc.total == expected.total == 16
    assert tc.get_uncommon_count() == expected.get_uncommon_count()


@pytest.mark.parametrize('cache_type', [LRI, LRU])
def test_cache_update_optional_source(cache_type):
    cache = cache_type(values={'existing': 0})
    cache.update()
    assert dict(cache) == {'existing': 0}
    cache.update(a=1)
    assert dict(cache) == {'existing': 0, 'a': 1}
    cache.update(cache, a=2)
    assert dict(cache) == {'existing': 0, 'a': 2}


class _FalseyCallable:
    def __bool__(self):
        return False

    def __call__(self, key):
        return 'miss'


@pytest.mark.parametrize('cache_type', [LRI, LRU])
def test_cache_falsey_on_miss(cache_type):
    cache = cache_type(on_miss=_FalseyCallable())
    assert cache['x'] == 'miss'
    assert dict(cache) == {'x': 'miss'}


def test_cachedmethod_bound_unbound_share_key():
    class C:
        def __init__(self):
            self.cache = {}
            self.calls = 0

        @cachedmethod('cache')
        def m(self, x):
            self.calls += 1
            return x * 2

    c = C()
    assert c.m(3) == 6
    assert C.m(c, 3) == 6
    assert c.calls == 1
    assert len(c.cache) == 1


@pytest.mark.parametrize('cache_type', [LRI, LRU])
def test_cache_copy(cache_type):
    on_miss = lambda k: k.upper()
    cache = cache_type(max_size=2, on_miss=on_miss)
    cache['a'] = 1
    cache['b'] = 2
    copied = cache.copy()
    assert cache.hit_count == 0
    assert copied.on_miss is on_miss
    assert copied == {'a': 1, 'b': 2}
    # insertion/access order is preserved: 'a' is evicted first
    copied['c'] = 3
    assert dict(copied) == {'b': 2, 'c': 3}


@pytest.mark.parametrize('cache_type', [LRI, LRU])
def test_cache_ior(cache_type):
    cache = cache_type(max_size=2)
    cache |= {'a': 1, 'b': 2, 'c': 3}
    assert len(cache) == 2
    assert dict(cache) == {'b': 2, 'c': 3}
    assert list(cache._link_lookup) == ['b', 'c']


def test_cache_eq_non_mapping():
    cache = LRI()
    assert (cache == None) is False
    assert cache != None
    assert (LRI(values={'a': 1}) == [1]) is False
    assert LRI(values={'a': 1}) == {'a': 1}
    assert LRI(values={'a': 1}) == LRU(values={'a': 1})


def test_threshold_counter_non_reciprocal_threshold():
    tc = ThresholdCounter(0.6)
    for _ in range(20):
        tc.add('a')
    assert tc.get('a') == 20


# --- signature-normalized cache keys -------------------------------------

LIST_CONVERTER = lambda value: ('list', tuple(value))
DICT_CONVERTER = lambda value: ('dict', tuple(sorted(value.items())))
NORMALIZING_CONVERTERS = {list: LIST_CONVERTER, dict: DICT_CONVERTER}


def test_signature_key_positional_keyword_default_share():
    def clean(text, strip=True):
        return text.strip() if strip else text

    builder = SignatureCacheKeyBuilder(clean)

    positional = builder.make_key(('  hi ',), {})
    keyword = builder.make_key((), {'text': '  hi '})
    explicit_default = builder.make_key(('  hi ', True), {})
    keyword_default = builder.make_key(('  hi ',), {'strip': True})

    assert positional == keyword == explicit_default == keyword_default
    assert hash(positional) == hash(keyword)

    different = builder.make_key(('  hi ', False), {})
    assert different != positional
    assert builder.make_key(('bye',), {}) != positional


def test_signature_key_memoizes_across_call_styles():
    cache = LRU()

    @cached(cache, converters=NORMALIZING_CONVERTERS)
    def clean(items, options=None, strict=True):
        clean.call_count += 1
        return sum(items)

    clean.call_count = 0

    assert clean([1, 2, 3]) == 6
    assert clean([1, 2, 3], None) == 6
    assert clean(items=[1, 2, 3]) == 6
    assert clean([1, 2, 3], options=None, strict=True) == 6
    assert clean(options=None, items=[1, 2, 3]) == 6
    assert clean.call_count == 1
    assert len(cache) == 1

    # genuinely different calls get their own entries
    assert clean([1, 2, 3], strict=False) == 6
    assert clean.call_count == 2
    assert clean([3, 2, 1]) == 6
    assert clean.call_count == 3


def test_signature_key_uses_registered_converters():
    def func(items, mapping):
        return items, mapping

    builder = SignatureCacheKeyBuilder(func, converters=NORMALIZING_CONVERTERS)

    via_args = builder.make_key(([1, 2], {'a': 1}), {})
    via_kwargs = builder.make_key((), {'items': [1, 2],
                                       'mapping': {'a': 1}})
    assert via_args == via_kwargs

    other = builder.make_key(([1, 2], {'a': 2}), {})
    assert other != via_args


def test_signature_key_unhashable_without_converter_is_uncacheable():
    class CustomUnhashable:
        __hash__ = None

    def func(blob):
        return blob

    builder = SignatureCacheKeyBuilder(func)
    with pytest.raises(UncacheableArgumentError):
        builder.make_key((CustomUnhashable(),), {})

    # registering a representation makes it cacheable
    registered = SignatureCacheKeyBuilder(
        func, converters={CustomUnhashable: lambda value: ('blob',)})
    assert registered.make_key((CustomUnhashable(),), {}) == registered.make_key(
        (), {'blob': CustomUnhashable()})


def test_signature_key_identity_only_object_is_uncacheable():
    class PlainInstance:
        def __init__(self, tag):
            self.tag = tag

    builder = SignatureCacheKeyBuilder(lambda blob: None)
    with pytest.raises(UncacheableArgumentError):
        builder.make_key((PlainInstance('a'),), {})

    # an explicitly registered converter declaring a value-based
    # representation is the supported escape hatch
    registered = SignatureCacheKeyBuilder(
        lambda blob: None,
        converters={PlainInstance: lambda value: ('blob', value.tag)})
    key = registered.make_key((PlainInstance('a'),), {})
    assert hash(key)
    assert key == registered.make_key((PlainInstance('a'),), {})


def test_uncacheable_call_bypasses_cache_but_still_runs():
    class MutableBlob:
        pass

    cache = LRU()

    @cached(cache, converters={int: lambda value: value})
    def work(amount, blob):
        work.call_count += 1
        return amount

    work.call_count = 0
    blob = MutableBlob()
    assert work(1, blob) == 1
    assert work(1, blob) == 1
    assert work.call_count == 2  # never cached, executed every time
    assert len(cache) == 0

    class UnhashableBlob:
        __hash__ = None

    assert work(2, UnhashableBlob()) == 2
    assert work.call_count == 3
    assert len(cache) == 0


def test_signature_key_varargs_do_not_collapse_distinct_calls():
    cache = LRU()

    @cached(cache, converters={})
    def variadic(*values):
        variadic.call_count += 1
        return values

    variadic.call_count = 0
    variadic(1, 2)
    variadic(1, 2)
    assert variadic.call_count == 1

    # one tuple argument is not the same call as two varargs
    variadic((1, 2))
    assert variadic.call_count == 2

    variadic(1, 2, 3)
    assert variadic.call_count == 3
    assert len(cache) == 3


def test_signature_key_kwargs_order_independent_but_distinct():
    cache = LRU()

    @cached(cache, converters={})
    def keyword_only(**options):
        keyword_only.call_count += 1
        return options

    keyword_only.call_count = 0
    keyword_only(a=1, b=2)
    keyword_only(b=2, a=1)
    assert keyword_only.call_count == 1

    keyword_only(a=1)
    assert keyword_only.call_count == 2


def test_signature_key_typed_and_container_kind_tags():
    builder = SignatureCacheKeyBuilder(lambda a, b: None)
    # numeric types collapse when typed is off, matching make_cache_key
    assert (builder.make_key((1, 1.0), {})
            == builder.make_key((1.0, 1), {}))

    typed_builder = SignatureCacheKeyBuilder(
        lambda a, b: None, typed=True)
    assert (typed_builder.make_key((1, 1.0), {})
            != typed_builder.make_key((1.0, 1), {}))

    # containers always carry kind markers: list != tuple of equal contents
    container_builder = SignatureCacheKeyBuilder(lambda a: None)
    assert (container_builder.make_key(([1, 2],), {})
            != container_builder.make_key(((1, 2),), {}))
    # list order is significant, set order is not
    assert (container_builder.make_key(([1, 2],), {})
            != container_builder.make_key(([2, 1],), {}))
    assert (container_builder.make_key(({1, 2},), {})
            == container_builder.make_key(({2, 1},), {}))


def test_signature_key_nested_containers_recurse():
    builder = SignatureCacheKeyBuilder(lambda a: None)
    value = [{'x': [1, 2]}, (3, {4, 5})]
    assert (builder.make_key((value,), {})
            == builder.make_key(([{'x': [1, 2]}, (3, {5, 4})],), {}))


def test_signature_key_converter_must_return_hashable():
    builder = SignatureCacheKeyBuilder(
        lambda a: None, converters={int: lambda value: [value]})
    with pytest.raises(UncacheableArgumentError):
        builder.make_key((1,), {})


def test_signature_key_builder_validates_converters():
    with pytest.raises(TypeError):
        SignatureCacheKeyBuilder(lambda a: None, converters={1: int})
    with pytest.raises(TypeError):
        SignatureCacheKeyBuilder(lambda a: None, converters={int: 3})


def test_cached_rejects_key_and_converters_together():
    with pytest.raises(TypeError):
        cached(LRU(), key=make_cache_key, converters={int: int})(lambda a: a)
    with pytest.raises(TypeError):
        cachedmethod('cache', key=make_cache_key,
                     converters={int: int})(lambda self, a: a)


def test_cachedmethod_normalized_bound_unbound_and_keyword_share():
    class Calculator:
        def __init__(self):
            self.cache = {}
            self.call_count = 0

        @cachedmethod('cache', converters={})
        def double(self, x):
            self.call_count += 1
            return x * 2

    one = Calculator()
    two = Calculator()

    assert one.double(3) == 6
    assert Calculator.double(one, 3) == 6
    assert one.double(x=3) == 6
    assert one.call_count == 1
    assert len(one.cache) == 1

    # a different instance must never read the first one's entries
    assert two.double(3) == 6
    assert two.call_count == 1
    assert len(two.cache) == 1


def test_cachedmethod_normalized_with_converters_and_varargs():
    class Parser:
        def __init__(self):
            self.cache = {}
            self.call_count = 0

        @cachedmethod('cache', converters=NORMALIZING_CONVERTERS)
        def run(self, rows, *flags):
            self.call_count += 1
            return len(rows) + len(flags)

    parser = Parser()
    assert parser.run([1, 2]) == 2
    assert Parser.run(parser, [1, 2]) == 2
    assert parser.run(rows=[1, 2]) == 2
    assert parser.call_count == 1

    # adding a vararg is a different call
    assert parser.run([1, 2], 1) == 3
    assert parser.call_count == 2

    # a single tuple argument is not the same call as one numeric vararg
    assert parser.run([1, 2], (1,)) == 3
    assert parser.call_count == 3


def test_cachedmethod_uncacheable_argument_bypasses_cache():
    class Session:
        pass

    class Service:
        def __init__(self):
            self.cache = {}
            self.call_count = 0

        @cachedmethod('cache', converters={int: lambda value: value})
        def handle(self, x, session):
            self.call_count += 1
            return x

    service = Service()
    session = Session()
    assert service.handle(1, session) == 1
    assert Service.handle(service, 1, session) == 1
    assert service.call_count == 2
    assert service.cache == {}


def test_make_signature_cache_key_helper():
    def func(a, b=2):
        return a + b

    first = make_signature_cache_key(func, (1,), {})
    second = make_signature_cache_key(func, (), {'a': 1})
    third = make_signature_cache_key(func, (1, 2), {})
    assert first == second == third


def test_signature_key_cyclic_container_is_uncacheable():
    cyclic = [1]
    cyclic.append(cyclic)
    builder = SignatureCacheKeyBuilder(lambda value: None)
    with pytest.raises(UncacheableArgumentError):
        builder.make_key((cyclic,), {})

    # shared (non-cyclic) references still normalize correctly
    shared = [1]
    diamond = [shared, shared]
    assert (builder.make_key((diamond,), {})
            == builder.make_key(([[1], [1]],), {}))


def test_signature_key_positional_only_and_keyword_only():
    cache = LRU()

    @cached(cache, converters={})
    def func(a, /, b, *, c=3):
        func.call_count += 1
        return (a, b, c)

    func.call_count = 0
    func(1, 2)
    func(1, 2, c=3)
    func(1, b=2)
    assert func.call_count == 1

    func(1, 2, c=4)
    assert func.call_count == 2


def test_signature_key_mutable_default_omitted_vs_explicit():
    cache = LRU()

    @cached(cache, converters={list: tuple})
    def func(items=()):
        func.call_count += 1
        return len(items)

    func.call_count = 0
    func()
    func(items=())
    assert func.call_count == 1

    func([1])
    assert func.call_count == 2


def test_signature_key_uncacheable_nested_in_container_bypasses():
    class Blob:
        pass

    cache = LRU()

    @cached(cache, converters={})
    def func(value):
        func.call_count += 1
        return value

    func.call_count = 0
    func([1, Blob()])
    func([1, Blob()])
    assert func.call_count == 2
    assert cache == {}


def test_cachedmethod_self_in_varargs_still_scopes_per_instance():
    class Service:
        def __init__(self):
            self.cache = {}
            self.call_count = 0

        @cachedmethod('cache', converters={})
        def method(*args):
            self = args[0]
            self.call_count += 1
            return args[1:]

    one = Service()
    two = Service()
    assert one.method(1, 2) == (1, 2)
    assert Service.method(one, 1, 2) == (1, 2)
    assert one.call_count == 1
    assert two.method(1, 2) == (1, 2)
    assert two.call_count == 1


def test_cachedmethod_unhashable_bound_instance_bypasses():
    class Unhashable:
        __hash__ = None

        def __init__(self):
            self.cache = {}
            self.call_count = 0

        @cachedmethod('cache', converters={})
        def method(self, x):
            self.call_count += 1
            return x

    instance = Unhashable()
    assert instance.method(5) == 5
    assert instance.method(5) == 5
    assert instance.call_count == 2
    assert instance.cache == {}
