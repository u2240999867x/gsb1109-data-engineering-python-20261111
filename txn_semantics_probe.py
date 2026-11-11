"""Minimal runnable probe of diskcache transaction semantics.

Run with the repo's diskcache importable (e.g. from the workspace root):
    python3 txn_semantics_probe.py

Covers:
  A. Same Cache object: auto-commit per op, explicit transact, nested transact.
  B. Two Cache objects on the same directory: lock timeout + visibility.
  C. FanoutCache: per-shard commits, transact() across shards, Timeout->False.
"""

import shutil
import sys
import tempfile
import time

sys.path.insert(0, '.')  # use the repo's diskcache, not an installed one

from diskcache import Cache, FanoutCache, Timeout

ROOT = tempfile.mkdtemp(prefix='dc-txn-probe-')
print('scratch dir:', ROOT)


def show(title):
    print('\n=== %s ===' % title)


# ---------------------------------------------------------------------------
# A. Same Cache object
# ---------------------------------------------------------------------------
show('A1: no explicit transact -> each op auto-commits; later failure keeps earlier writes')
cache = Cache(tempfile.mkdtemp(dir=ROOT), timeout=0.2)
cache.set('a1', 1)                       # commits immediately
try:
    with cache.transact():
        cache.set('a2', 2)
        raise RuntimeError('boom')
except RuntimeError:
    pass
print('a1 =', cache.get('a1'), '(committed before the failed block)')
print('a2 =', cache.get('a2'), '(rolled back with the failed transact)')

show('A2: nested transact, inner raises but is caught inside outer -> inner writes COMMIT')
cache = Cache(tempfile.mkdtemp(dir=ROOT), timeout=0.2)
with cache.transact():
    cache.set('outer', 1)
    try:
        with cache.transact():           # same thread -> no new BEGIN (core.py:716)
            cache.set('inner', 2)
            raise RuntimeError('inner boom')
    except RuntimeError:
        print('caught inner error; inner txn did NOT roll back (begin=False)')
print('outer =', cache.get('outer'), ' inner =', cache.get('inner'),
      '<- inner write survived because only the outermost txn commits/rolls back')

show('A3: nested transact, inner error propagates -> whole outer txn rolls back')
cache = Cache(tempfile.mkdtemp(dir=ROOT), timeout=0.2)
try:
    with cache.transact():
        cache.set('outer', 1)
        with cache.transact():
            cache.set('inner', 2)
            raise RuntimeError('inner boom')
except RuntimeError:
    pass
print('outer =', cache.get('outer'), ' inner =', cache.get('inner'),
      '(both rolled back)')

# ---------------------------------------------------------------------------
# B. Two Cache objects on the same directory
# ---------------------------------------------------------------------------
show('B1: two Cache objects, same dir -> separate connections, independent commits')
directory = tempfile.mkdtemp(dir=ROOT)
cache1 = Cache(directory, timeout=0.2)
cache2 = Cache(directory, timeout=0.2)
cache1.set('from-c1', 1)
print('cache2 sees from-c1 =', cache2.get('from-c1'),
      '(auto-commit makes it visible across objects)')

show('B2: cache1 holds transact(); cache2 write hits lock timeout -> Timeout, nothing written')
with cache1.transact():
    cache1.set('held-by-c1', 1)
    start = time.time()
    try:
        cache2.set('blocked', 1, retry=False)
        print('unexpected: cache2 write succeeded')
    except Timeout:
        elapsed = time.time() - start
        print('cache2.set raised Timeout after %.2fs (connection timeout=0.2s)' % elapsed)
print('after cache1 commits:')
print('  held-by-c1 =', cache2.get('held-by-c1'), '(committed on transact exit)')
print('  blocked    =', cache2.get('blocked'), '(failed op rolled back, key absent)')

show('B3: cache2 partial batch while cache1 holds the lock -> earlier ops already committed')
cache2.set('b3-first', 1)                # commits on its own
with cache1.transact():
    try:
        cache2.set('b3-second', 2)
    except Timeout:
        print('b3-second timed out')
print('b3-first =', cache2.get('b3-first'), '(still there: per-op auto-commit)')
print('b3-second =', cache2.get('b3-second'))

# ---------------------------------------------------------------------------
# C. FanoutCache shards
# ---------------------------------------------------------------------------
show('C1: FanoutCache without transact -> each key commits in its own shard txn')
directory = tempfile.mkdtemp(dir=ROOT)
fanout = FanoutCache(directory, shards=4, timeout=0.05)
# pick keys that land on distinct shards
by_shard = {}
candidate = 0
while len(by_shard) < 3:
    key = 'key-%d' % candidate
    by_shard.setdefault(fanout._hash(key) % fanout._count, key)
    candidate += 1
keys = [by_shard[i] for i in sorted(by_shard)]
print('keys -> shards:', {k: fanout._hash(k) % fanout._count for k in keys})
try:
    for num, key in enumerate(keys):
        if num == 2:
            raise RuntimeError('ETL step failed mid-batch')
        fanout.set(key, num)
except RuntimeError as error:
    print(error)
print('after failure:', {k: fanout.get(k) for k in keys},
      '<- first two keys committed on different shards; no cross-shard rollback')

show('C2: FanoutCache.transact() -> all shards roll back together on error')
fanout = FanoutCache(tempfile.mkdtemp(dir=ROOT), shards=4, timeout=0.05)
try:
    with fanout.transact():              # ExitStack over all shard txns (fanout.py:96)
        for num, key in enumerate(keys):
            fanout.set(key, num)
        raise RuntimeError('boom')
except RuntimeError:
    pass
print('after failure:', {k: fanout.get(k) for k in keys}, '(all rolled back)')

show('C3: FanoutCache.set swallows lock Timeout -> returns False, no exception')
directory = tempfile.mkdtemp(dir=ROOT)
fanout = FanoutCache(directory, shards=2, timeout=0.05)
blocker = Cache(fanout._shards[0].directory, timeout=0.05)
key_on_shard0 = next(
    'k%d' % i for i in range(1000)
    if fanout._hash('k%d' % i) % fanout._count == 0
)
with blocker.transact():
    ok = fanout.set(key_on_shard0, 'v', retry=False)
    print('fanout.set returned', ok, '(silently False; shard-0 db locked by blocker)')
print('after blocker commits:', key_on_shard0, '=', fanout.get(key_on_shard0))

shutil.rmtree(ROOT, ignore_errors=True)
print('\ndone.')
