"""Test scoped views of diskcache.Cache and diskcache.FanoutCache."""

import pickle
import shutil
import time

import pytest

import diskcache as dc


@pytest.fixture
def cache():
    with dc.Cache() as cache:
        yield cache
    shutil.rmtree(cache.directory, ignore_errors=True)


@pytest.fixture
def fanout():
    with dc.FanoutCache(shards=2) as cache:
        yield cache
    shutil.rmtree(cache.directory, ignore_errors=True)


def test_view_constructor_scope(cache):
    assert cache.scope is None
    assert cache.view('alpha').scope == 'alpha'

    with pytest.raises(ValueError):
        cache.view(None)

    with pytest.raises(ValueError):
        cache.view(lambda: None)

    with dc.Cache(cache.directory, scope='alpha') as view:
        assert view.scope == 'alpha'


def test_view_read_write_isolation(cache):
    alpha = cache.view('alpha')
    beta = cache.view('beta')

    alpha.set('key', 'alpha-value')
    beta.set('key', 'beta-value')

    assert alpha.get('key') == 'alpha-value'
    assert beta.get('key') == 'beta-value'
    assert cache.get('key') is None
    assert 'key' not in cache
    assert 'key' in alpha and 'key' in beta

    assert alpha.delete('key')
    assert alpha.get('key') is None
    assert beta.get('key') == 'beta-value'

    assert beta.pop('key') == 'beta-value'
    assert beta.get('key') is None


def test_view_no_legacy_migration(cache):
    cache.set('legacy', 1, tag='legacy-tag')

    alpha = cache.view('alpha')

    assert alpha.get('legacy') is None
    assert 'legacy' not in alpha
    assert list(alpha) == []
    assert len(alpha) == 0
    assert alpha.evict('legacy-tag') == 0

    assert cache.get('legacy') == 1
    assert cache.evict('legacy-tag') == 1


def test_view_iteration(cache):
    alpha = cache.view('alpha')
    beta = cache.view('beta')

    for value in [3, 1, 2]:
        alpha[value] = value * 10
    for value in [6, 4, 5]:
        beta[value] = value * 10

    assert list(alpha) == [3, 1, 2]
    assert list(reversed(alpha)) == [2, 1, 3]
    assert list(alpha.iterkeys()) == [1, 2, 3]
    assert list(alpha.iterkeys(reverse=True)) == [3, 2, 1]
    assert list(beta) == [6, 4, 5]

    assert alpha.peekitem() == (2, 20)
    assert alpha.peekitem(last=False) == (3, 30)
    assert beta.peekitem() == (5, 50)

    assert len(alpha) == 3
    assert len(beta) == 3
    assert len(cache) == 0

    alpha.clear()
    assert list(alpha) == []
    assert len(beta) == 3


def test_view_tag_independence(cache):
    alpha = cache.view('alpha')
    beta = cache.view('beta')

    for num in range(5):
        alpha.set(num, num, tag='shared')
    for num in range(5, 10):
        beta.set(num, num, tag='shared')

    assert alpha.evict('shared') == 5
    assert len(alpha) == 0
    assert len(beta) == 5

    for num in range(5, 10):
        assert beta.get(num) == num

    assert beta.evict('shared') == 5
    assert len(beta) == 0


def test_view_tag_values(cache):
    alpha = cache.view('alpha')
    beta = cache.view('beta')

    alpha.set('key', 'alpha', tag='blue')
    beta.set('key', 'beta', tag='blue')

    assert alpha.get('key', tag=True) == ('alpha', 'blue')
    assert beta.get('key', tag=True) == ('beta', 'blue')
    assert alpha.peekitem(tag=True) == (('key', 'alpha'), 'blue')

    assert alpha.pop('key', tag=True) == ('alpha', 'blue')
    assert beta.get('key', tag=True) == ('beta', 'blue')


def test_view_expire_and_cull(cache):
    alpha = cache.view('alpha')
    beta = cache.view('beta')

    alpha.set('expire', 1, expire=0.001)
    beta.set('expire', 2, expire=0.001)

    time.sleep(0.01)

    assert alpha.expire() == 1
    assert alpha.get('expire') is None
    assert beta.get('expire') is None
    assert len(beta) == 1

    alpha.set('big', b'A' * 200000)
    beta.set('big', b'B' * 200000)

    assert alpha.volume() == 200000
    assert beta.volume() == 200000
    assert cache.volume() > 0

    alpha.clear()
    assert alpha.volume() == 0
    assert beta.volume() == 200000


def test_view_stats(cache):
    alpha = cache.view('alpha')
    beta = cache.view('beta')

    assert alpha.stats(enable=True) == (0, 0)
    assert beta.stats(enable=True) == (0, 0)

    alpha.set('key', 1)
    beta.set('key', 2)

    alpha.get('key')
    alpha.get('key')
    alpha.get('missing')
    beta.get('missing')

    assert alpha.stats(enable=False) == (2, 1)
    assert beta.stats(enable=False) == (0, 1)

    assert alpha.stats(enable=True, reset=True) == (2, 1)
    assert alpha.stats(enable=False) == (0, 0)
    assert beta.stats(enable=False) == (0, 1)


def test_view_queue(cache):
    alpha = cache.view('alpha')
    beta = cache.view('beta')

    alpha_key = alpha.push('alpha')
    beta_key = beta.push('beta')

    assert alpha_key == beta_key == 500000000000000
    assert alpha.pull() == (alpha_key, 'alpha')
    assert beta.pull() == (beta_key, 'beta')

    alpha.push('alpha', prefix='queue')
    beta.push('beta', prefix='queue')

    assert alpha.peek(prefix='queue') == ('queue-500000000000000', 'alpha')
    assert beta.peek(prefix='queue') == ('queue-500000000000000', 'beta')


def test_view_memoize(cache):
    alpha = cache.view('alpha')
    beta = cache.view('beta')

    calls = []

    @alpha.memoize(expire=10, tag='memo')
    def function(value):
        calls.append(value)
        return value * 2

    assert function(3) == 6
    assert function(3) == 6
    assert calls == [3]
    assert alpha.evict('memo') == 1

    @beta.memoize(expire=10, tag='memo')
    def function_beta(value):
        return value * 3

    assert function_beta(3) == 9
    assert beta.evict('memo') == 1
    assert alpha.evict('memo') == 0


def test_view_pickle(cache):
    alpha = cache.view('alpha')
    alpha.set('key', 'value')

    view = pickle.loads(pickle.dumps(alpha))
    assert view.scope == 'alpha'
    assert view.get('key') == 'value'
    view.close()

    legacy = dc.Cache.__new__(dc.Cache)
    legacy.__setstate__((cache.directory, cache.timeout, type(cache.disk)))
    assert legacy.scope is None
    assert legacy.get('key') is None
    legacy.close()


def test_fanout_view(fanout):
    alpha = fanout.view('alpha')
    beta = fanout.view('beta')

    assert alpha.scope == 'alpha'
    assert beta.scope == 'beta'

    alpha.set('key', 'alpha')
    beta.set('key', 'beta')

    assert alpha.get('key') == 'alpha'
    assert beta.get('key') == 'beta'
    assert fanout.get('key') is None

    assert list(alpha) == ['key']
    assert list(beta) == ['key']
    assert len(alpha) == len(beta) == 1
    assert len(fanout) == 0

    for num in range(10):
        alpha.set(num, num, tag='shared')
        beta.set(num + 10, num, tag='shared')

    assert alpha.evict('shared') == 10
    assert len(alpha) == 1
    assert len(beta) == 11

    assert alpha.stats(enable=True)
    alpha.get('missing')
    assert alpha.stats(enable=False) == (0, 1)


def test_fanout_view_helpers(fanout):
    alpha = fanout.view('alpha')

    cache = alpha.cache('test')
    assert cache.scope == 'alpha'
    cache.set('key', 1)
    assert cache.get('key') == 1

    deque = alpha.deque('test')
    deque.extend('abc')
    assert deque.popleft() == 'a'

    index = alpha.index('test')
    index['abc'] = 1
    assert index['abc'] == 1


def test_fanout_view_pickle(fanout):
    alpha = fanout.view('alpha')
    alpha.set('key', 'value')

    view = pickle.loads(pickle.dumps(alpha))
    assert view.scope == 'alpha'
    assert view.get('key') == 'value'
    view.close()
