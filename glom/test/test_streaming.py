import pytest

from itertools import count, dropwhile, chain

from glom import Iter
from glom import glom, SKIP, STOP, T, Call, Spec, Glommer, Check, SKIP
from glom import IterRetry, RetryError


RANGE_5 = list(range(5))


def test_iter():
    assert list(glom(['1', '2', '3'], Iter(int))) == [1, 2, 3]
    cnt = count()
    cnt_1 = glom(cnt, Iter(lambda t: t + 1))
    assert (next(cnt_1), next(cnt_1)) == (1, 2)
    assert next(cnt) == 2

    assert list(glom(['1', '2', '3'], (Iter(int), enumerate))) == [(0, 1), (1, 2), (2, 3)]

    assert list(glom([1, SKIP, 2], Iter())) == [1, 2]
    assert list(glom([1, STOP, 2], Iter())) == [1]

    with pytest.raises(TypeError):
        Iter(nonexistent_kwarg=True)


def test_filter():
    is_odd = lambda x: x % 2
    odd_spec = Iter().filter(is_odd)
    out = glom(RANGE_5, odd_spec)
    assert list(out) == [1, 3]

    # let's just make sure we're actually streaming just in case
    counter = count()
    out = glom(counter, odd_spec)
    assert next(out) == 1
    assert next(out) == 3
    assert next(counter) == 4
    assert next(counter) == 5
    assert next(out) == 7

    bools = [True, False, False, True, False]
    spec = Iter().filter().all()
    out = glom(bools, spec)
    assert out == [True, True]

    imags = [0j, 1j, 2, 2j, 3j]
    spec = Iter().filter(Check(T.imag.real, type=float, one_of=(0, 2), default=SKIP)).all()
    out = glom(imags, spec)
    assert out == [0j, 2j]

    assert repr(Iter().filter(T.a.b)) == 'Iter().filter(T.a.b)'
    assert repr(Iter(list).filter(sum)) == 'Iter(list).filter(sum)'


def test_map():
    spec = Iter().map(lambda x: x * 2)
    out = glom(RANGE_5, spec)
    assert list(out) == [0, 2, 4, 6, 8]
    assert repr(Iter().map(T.a.b)) == 'Iter().map(T.a.b)'


def test_split_flatten():
    falsey_stream = [1, None, None, 2, 3, None, 4]
    spec = Iter().split()
    out = glom(falsey_stream, spec)
    assert list(out) == [[1], [2, 3], [4]]

    spec = Iter().split().flatten()
    out = glom(falsey_stream, spec)
    assert list(out) == [1, 2, 3, 4]

    assert repr(Iter().split(sep=None, maxsplit=2)) == 'Iter().split(sep=None, maxsplit=2)'
    assert repr(Iter(T.a.b[1]).flatten()) == 'Iter(T.a.b[1]).flatten()'


def test_chunked():
    int_list = list(range(9))

    spec = Iter().chunked(3)
    out = glom(int_list, spec)
    assert list(out) == [[0, 1, 2], [3, 4, 5], [6, 7, 8]]

    spec = Iter().chunked(3).map(sum)
    out = glom(int_list, spec)
    assert list(out) == [3, 12, 21]


def test_windowed():
    int_list = list(range(5))

    spec = Iter().windowed(3)
    out = glom(int_list, spec)
    assert list(out) == [(0, 1, 2), (1, 2, 3), (2, 3, 4)]
    assert repr(spec) == 'Iter().windowed(3)'

    spec = spec.filter(lambda x: bool(x[0] % 2)).map(sum)
    out = glom(int_list, spec)
    assert next(out) == 6

    out = glom(range(10), spec)
    assert list(out) == [6, 12, 18, 24]


def test_unique():
    int_list = list(range(10))

    spec = Iter().unique()
    out = glom(int_list, spec)
    assert list(out) == int_list

    spec = Iter(lambda x: x % 4).unique()
    out = glom(int_list, spec)
    assert list(out) == int_list[:4]
    assert repr(Iter().unique(T.a)) == 'Iter().unique(T.a)'


def test_slice():
    cnt = count()

    spec = Iter().slice(3)
    out = glom(cnt, spec)

    assert list(out) == [0, 1, 2]
    assert next(cnt) == 3

    out = glom(range(10), Iter().slice(1, 5))
    assert list(out) == [1, 2, 3, 4]

    out = glom(range(10), Iter().slice(1, 6, 2))
    assert list(out) == [1, 3, 5]
    assert repr(Iter().slice(1, 6, 2)) == 'Iter().slice(1, 6, 2)'

    out = glom(range(10), Iter().limit(3))
    assert list(out) == [0, 1, 2]
    assert repr(Iter().limit(3)) == 'Iter().limit(3)'

    out = glom(range(5), Iter().limit(10))
    assert list(out) == [0, 1, 2, 3, 4]

    # test broken args
    with pytest.raises(TypeError):
        Iter().slice(1, 2, 3, 4)


def test_while():
    cnt = count()
    out = glom(cnt, Iter().takewhile(lambda x: x < 3))
    assert list(out) == [0, 1, 2]
    assert next(cnt) == 4
    assert repr(Iter().takewhile(T.a) == 'Iter().takewhile(T.a)')

    range_iter = iter(range(7))
    out = glom(range_iter, Iter().dropwhile(lambda x: x < 3 or x > 5))
    assert list(out) == [3, 4, 5, 6]  # 6 still here despite the x>5 above

    out = glom(range(10), Iter().dropwhile(lambda x: x >= 0).limit(10))
    assert list(out) == []

    out = glom(range(8), Iter().dropwhile((T.bit_length(), lambda x: x < 3)))
    assert list(out) == [4, 5, 6, 7]
    assert repr(Iter().dropwhile(T.a) == 'Iter().dropwhile(T.a)')


def test_iter_composition():
    int_list = list(range(10))
    out = glom(int_list, (Iter(), Iter(), list))
    assert out == int_list

    out = glom([int_list] * 3, Iter(Iter(lambda x: x % 4)).flatten().unique())
    assert list(out) == [0, 1, 2, 3]


def test_faulty_iterate():
    glommer = Glommer()

    def bad_iter(obj):
        raise RuntimeError('oops')

    glommer.register(str, iterate=bad_iter)

    with pytest.raises(TypeError):
        glommer.glom('abc', (Iter(), list))


def test_first():
    spec = Iter().first(T.imag)

    target = iter([1, 2, 3j, 4])
    out = glom(target, spec)
    assert out == 3j
    assert next(target) == 4
    assert repr(spec) == '(Iter(), First(T.imag))'

    spec = Iter().first(T.imag, default=0)
    target = iter([1, 2, 4])
    out = glom(target, spec)
    assert out == 0
    assert repr(spec) == '(Iter(), First(T.imag, default=0))'


def test_all():
    int_iter = iter(range(10))

    out = glom(int_iter, Iter().all())
    assert out == list(range(10))
    assert next(int_iter, None) is None
    assert repr(Iter().all()) == 'Pipe(Iter(), list)'


def _make_flaky(fail_times):
    # call N times per value before succeeding, tracking every invocation
    calls = []

    def flaky(value):
        seen = sum(1 for v in calls if v == value)
        calls.append(value)
        if seen < fail_times:
            raise ValueError('transient failure for %r' % (value,))
        return value * 10

    flaky.calls = calls
    return flaky


def test_retry_succeeds_without_reprocessing_prior_elements():
    flaky = _make_flaky(fail_times=2)
    out = glom([1, 2], Iter().map(flaky).retry(3).all())
    assert out == [10, 20]
    # each element attempted up to 3 times, but element 1 is never redone
    # once it has succeeded and element 2 is being processed.
    assert flaky.calls == [1, 1, 1, 2, 2, 2]


def test_retry_is_opt_in():
    flaky = _make_flaky(fail_times=5)
    with pytest.raises(ValueError):
        glom([1, 2], Iter().map(flaky).all())
    # without retry() the first failure aborts and nothing is retried
    assert flaky.calls == [1]


def test_retry_exhausted_raises_retry_error():
    def always_fails(value):
        raise RuntimeError('service unavailable')

    with pytest.raises(RetryError) as exc_info:
        glom(['a', 'b', 'c'], Iter().map(always_fails).retry(2).all())

    err = exc_info.value
    assert err.target == 'a'               # original input retained
    assert err.attempts == 2               # total attempts recorded
    assert isinstance(err.exc, RuntimeError)  # final reason retained
    assert err.path.values()[-1] == 0      # input position retained
    assert '2 attempt' in str(err)


def test_retry_non_retryable_exception_propagates_immediately():
    seen = []

    def raise_keyerror(value):
        seen.append(value)
        raise KeyError('not configured for retry')

    with pytest.raises(KeyError):
        glom([1, 2], Iter().map(raise_keyerror)
             .retry(3, exceptions=ValueError).all())
    assert seen == [1]  # no retries, next element never reached


def test_retry_accepts_exception_tuple():
    def raise_keyerror(value):
        raise KeyError()

    with pytest.raises(RetryError) as exc_info:
        glom([1], Iter().map(raise_keyerror)
             .retry(2, exceptions=(ValueError, KeyError)).all())
    assert exc_info.value.attempts == 2


def test_retry_attempts_one_runs_once():
    invocations = []

    def fail(value):
        invocations.append(value)
        raise ValueError()

    with pytest.raises(RetryError) as exc_info:
        glom([1, 2], Iter().map(fail).retry(1).all())
    assert invocations == [1]
    assert exc_info.value.attempts == 1


def test_retry_does_not_recreate_input_iterator():
    iterated = []

    class SinglePassIterator:
        def __init__(self, data):
            self._it = iter(data)

        def __iter__(self):
            iterated.append(id(self))
            return self

        def __next__(self):
            return next(self._it)

    source = SinglePassIterator([1, 2, 3])
    flaky = _make_flaky(fail_times=1)
    out = glom(source, Iter().map(flaky).retry(3).all())

    assert out == [10, 20, 30]
    # the very same iterator object is driven throughout; never rebuilt
    assert set(iterated) == {id(source)}


def test_retry_only_reruns_failed_transform():
    order = []

    def stage(value):
        order.append(('stage', value))
        return value

    def fail_on_two(value):
        order.append(('fail', value))
        if value == 2:
            raise ValueError('boom')
        return value

    with pytest.raises(RetryError) as exc_info:
        glom([1, 2, 3], Iter().map(stage).map(fail_on_two).retry(2).all())

    assert exc_info.value.target == 2
    # stage(2) ran once; only fail_on_two(2) was retried, and element 1
    # (fully successful) was never touched again.
    assert order == [('stage', 1), ('fail', 1),
                     ('stage', 2), ('fail', 2), ('fail', 2)]


def test_retry_delay_numeric_and_callable():
    import time

    callable_delays = []
    config = IterRetry(3, delay=lambda n: callable_delays.append(n) or 0)
    flaky = _make_flaky(fail_times=2)
    glom([1], Iter().map(flaky).retry(config).all())
    assert callable_delays == [0, 1]

    flaky = _make_flaky(fail_times=1)
    start = time.time()
    glom([1], Iter().map(flaky).retry(2, delay=0.02).all())
    assert time.time() - start >= 0.02


def test_retry_on_base_subspec():
    flaky = _make_flaky(fail_times=1)
    out = glom([5], Iter(flaky).retry(2).all())
    assert out == [50]


def test_retry_works_for_each_per_element_op():
    def flaky(base):
        state = {'n': 0}

        def predicate(value):
            state['n'] += 1
            if state['n'] == 1:
                raise RuntimeError('transient')
            return base(value)

        return predicate

    assert glom(range(5),
                Iter().filter(flaky(lambda x: x % 2)).retry(3).all()) == [1, 3]
    assert glom(range(6),
                Iter().unique(flaky(lambda x: x % 3)).retry(3).all()) == [0, 1, 2]
    assert glom([1, 2, 3],
                Iter().takewhile(flaky(lambda x: x < 3)).retry(3).all()) == [1, 2]
    assert glom([0, 1, 2],
                Iter().dropwhile(flaky(lambda x: x < 1)).retry(3).all()) == [1, 2]


def test_retry_lazy_stream_lets_caller_choose_stop_or_continue():
    def lookup(value):
        if value == 2:
            raise ValueError('reference service down')
        return value * 10

    stream = glom([1, 2, 3, 4], Iter().map(lookup).retry(2))
    results, dead_lettered = [], []

    while True:
        try:
            results.append(next(stream))
        except StopIteration:
            break
        except RetryError as err:
            # caller chooses to keep going on the SAME stream; the
            # underlying iterator is not recreated or rewound
            dead_lettered.append((err.target, err.path.values()[-1], err.exc))

    assert results == [10, 30, 40]
    assert len(dead_lettered) == 1
    assert dead_lettered[0][0] == 2
    assert dead_lettered[0][1] == 1


def test_retry_nested_iter_isolation():
    def inner_fail(value):
        raise RuntimeError('inner')

    spec = (Iter()
            .map(lambda row: glom(row, Iter().map(inner_fail).retry(1).all())),
            list)
    with pytest.raises(RetryError) as exc_info:
        glom([[1, 2]], spec)
    assert exc_info.value.target == 1

    # a subsequent plain Iter does not inherit the retry configuration
    seen = []

    def no_retry(value):
        seen.append(value)
        raise ValueError()

    with pytest.raises(ValueError):
        glom([9], Iter().map(no_retry).all())
    assert seen == [9]


def test_retry_shared_config_object():
    config = IterRetry(3, exceptions=(ValueError,))
    flaky = _make_flaky(fail_times=2)
    out = glom([1], Iter().map(flaky).retry(config).all())
    assert out == [10]
    assert repr(Iter().retry(config)) == 'Iter().retry(3, exceptions=(ValueError,))'


def test_retry_config_validation_and_repr():
    for bad in (0, -1, 1.5, True, '3'):
        with pytest.raises(ValueError):
            IterRetry(bad)

    with pytest.raises(TypeError):
        Iter().retry('not-a-config')

    assert repr(IterRetry(3)) == 'IterRetry(3)'
    assert repr(Iter().map(int).retry(3)) == 'Iter().retry(3).map(int)'
    assert repr(Iter(int).retry(2, exceptions=ValueError, delay=0.1)) == (
        "Iter(int).retry(2, delay=0.1, exceptions=ValueError)")

