"""Test diskcache composite (source, business_time) index."""

import os
import os.path as op
import shutil
import sqlite3
import tempfile
import time

import pytest

import diskcache as dc
from diskcache.core import METADATA


@pytest.fixture
def cache():
    with dc.Cache(composite_index=True) as cache:
        yield cache
    shutil.rmtree(cache.directory, ignore_errors=True)


def make_key(source, business_time, event_id):
    return {
        'source': source,
        'business_time': business_time,
        'event_id': event_id,
    }


def seed(cache):
    entries = [
        # event_id is the stable primary key; business_time has ties.
        ('binlog', 3, 'e3', 'v3'),
        ('binlog', 1, 'e1', 'v1'),
        ('binlog', 2, 'e2', 'v2'),
        ('binlog', 1, 'e4', 'v4'),
        ('kafka', 7, 'k1', 'k1-value'),
    ]
    for source, business_time, event_id, value in entries:
        cache.set(
            make_key(source, business_time, event_id), value
        )


def test_setting_enabled():
    with dc.Cache(composite_index=True) as cache:
        assert cache.composite_index == 1
        rows = cache._sql(
            "SELECT name FROM sqlite_master"
            " WHERE type = 'index' AND name = 'Cache_composite'"
        ).fetchall()
        assert rows == [('Cache_composite',)]
    shutil.rmtree(cache.directory, ignore_errors=True)


def test_setting_default_disabled():
    with dc.Cache() as cache:
        assert cache.composite_index == 0
        assert cache._sql(
            "SELECT name FROM sqlite_master"
            " WHERE type = 'index' AND name = 'Cache_composite'"
        ).fetchall() == []
    shutil.rmtree(cache.directory, ignore_errors=True)


def test_select_ordered_by_business_time_then_primary_key(cache):
    seed(cache)
    results = cache.select('binlog')
    assert [key['event_id'] for key, _ in results] == [
        'e1',
        'e4',
        'e2',
        'e3',
    ]
    assert [value for _, value in results] == ['v1', 'v4', 'v2', 'v3']


def test_select_closed_range(cache):
    seed(cache)
    results = cache.select('binlog', start=1, end=2)
    assert [key['event_id'] for key, _ in results] == ['e1', 'e4', 'e2']


def test_select_open_ranges(cache):
    seed(cache)
    assert [
        key['event_id'] for key, _ in cache.select('binlog', start=2)
    ] == ['e2', 'e3']
    assert [
        key['event_id'] for key, _ in cache.select('binlog', end=1)
    ] == ['e1', 'e4']


def test_select_limit(cache):
    seed(cache)
    results = cache.select('binlog', limit=2)
    assert len(results) == 2
    assert [key['event_id'] for key, _ in results] == ['e1', 'e4']


def test_select_unknown_source(cache):
    seed(cache)
    assert cache.select('unknown') == []


def test_update_keeps_index_in_sync(cache):
    seed(cache)
    key = make_key('binlog', 1, 'e1')
    cache.set(key, 'v1-updated')

    results = cache.select('binlog', start=1, end=1)
    assert [(key['event_id'], value) for key, value in results] == [
        ('e1', 'v1-updated'),
        ('e4', 'v4'),
    ]


def test_delete_keeps_index_in_sync(cache):
    seed(cache)
    key = make_key('binlog', 1, 'e1')
    del cache[key]

    assert [
        key['event_id'] for key, _ in cache.select('binlog', start=1, end=1)
    ] == ['e4']
    assert cache._sql(
        'SELECT COUNT(*) FROM Cache WHERE index_source = ?', ('binlog',)
    ).fetchone()[0] == 3


def test_pop_keeps_index_in_sync(cache):
    seed(cache)
    key = make_key('kafka', 7, 'k1')
    assert cache.pop(key) == 'k1-value'
    assert cache.select('kafka') == []


def test_add_and_incr_keep_index_in_sync(cache):
    key = make_key('binlog', 1, 'counter')
    assert cache.add(key, 10)
    assert cache.select('binlog') == [(key, 10)]
    assert cache.incr(key, 5) == 15
    assert cache.select('binlog') == [(key, 15)]
    assert cache.decr(key, 2) == 13
    assert cache.select('binlog')[0][1] == 13


def test_select_read_returns_file_handle(cache):
    key = make_key('binlog', 1, 'big')
    payload = b'abcd' * 2**14  # Larger than disk_min_file_size (32kb).
    cache.set(key, payload)

    results = cache.select('binlog', read=True)
    assert len(results) == 1
    returned_key, handle = results[0]
    assert returned_key == key
    try:
        assert handle.read() == payload
    finally:
        handle.close()


def test_missing_value_file_excluded(cache):
    key = make_key('binlog', 1, 'file-backed')
    payload = b'abcd' * 2**14
    cache.set(key, payload)

    rows = cache._sql(
        'SELECT filename FROM Cache WHERE index_source = ?', ('binlog',)
    ).fetchall()
    ((filename,),) = rows
    os.remove(op.join(cache.directory, filename))

    # Index still points at the row but the dangling entry is not valid.
    assert cache.select('binlog') == []


def test_clear_keeps_index_in_sync(cache):
    seed(cache)
    assert cache.clear() == 5
    assert cache.select('binlog') == []
    assert cache.select('kafka') == []


def test_expire_keeps_index_in_sync(cache):
    cache.set(make_key('t', 1, 'live'), 'live')
    cache.set(make_key('t', 2, 'gone'), 'gone', expire=0.01)
    time.sleep(0.05)

    # The expired row still occupies an index slot but is not returned.
    assert [
        value for _, value in cache.select('t')
    ] == ['live']
    assert cache.expire() == 1
    assert [
        value for _, value in cache.select('t')
    ] == ['live']


def test_missing_field_rejected(cache):
    with pytest.raises(dc.IndexKeyError):
        cache.set({'source': 'binlog'}, 'value')
    with pytest.raises(dc.IndexKeyError):
        cache.set({'business_time': 1}, 'value')


def test_non_mapping_key_rejected(cache):
    for bad_key in ('plain', 1, 1.5, b'bytes', ('tuple',)):
        with pytest.raises(dc.IndexKeyError):
            cache.set(bad_key, 'value')


def test_explicit_none_distinct_from_missing(cache):
    # Explicit None is allowed and stored as SQL NULL.
    key = make_key(None, None, 'null-event')
    cache.set(key, 'null-value')
    assert cache.select(None) == [(key, 'null-value')]

    # NULL business time never matches a bounded range.
    assert cache.select(None, start=0) == []
    assert cache.select(None, end=10) == []

    # A missing field is still an error for the same key shape.
    with pytest.raises(dc.IndexKeyError):
        cache.set({'source': None}, 'value')


def test_expired_entry_not_valid_result(cache):
    cache.set(make_key('t', 5, 'x'), 'exp', expire=0.01)
    time.sleep(0.05)

    # The row and its index entry linger until culling.
    assert cache._sql(
        'SELECT COUNT(*) FROM Cache WHERE index_source = ?', ('t',)
    ).fetchone()[0] == 1

    # But the index must not surface it as a valid result.
    assert cache.select('t') == []
    assert cache.select('t', start=0, end=10) == []


def test_limit_not_starved_by_stale_entries(cache):
    # Two expired entries sort ahead of two valid entries.
    for business_time in (1, 2):
        cache.set(
            make_key('t', business_time, 'old-%d' % business_time),
            'old',
            expire=0.01,
        )
    for business_time in (3, 4):
        cache.set(make_key('t', business_time, 'new-%d' % business_time), 'new')

    time.sleep(0.05)

    results = cache.select('t', limit=2)
    assert [value for _, value in results] == ['new', 'new']
    assert [
        key['business_time'] for key, _ in results
    ] == [3, 4]


def test_limit_pages_through_null_business_time(cache):
    cache.set(make_key(None, None, 'n1'), 'n1')
    cache.set(make_key(None, None, 'n2'), 'n2')
    cache.set(make_key(None, 5, 'a'), 'a')
    cache.set(make_key(None, 6, 'b'), 'b')

    full = [value for _, value in cache.select(None)]
    assert full == ['n1', 'n2', 'a', 'b']

    for limit in (1, 2, 3, 4):
        assert [
            value for _, value in cache.select(None, limit=limit)
        ] == full[:limit]


def test_select_requires_enabled_index():
    with dc.Cache() as cache:
        with pytest.raises(dc.IndexKeyError):
            cache.select('binlog')
    shutil.rmtree(cache.directory, ignore_errors=True)


def test_create_and_drop_at_runtime():
    with dc.Cache() as cache:
        key = make_key('binlog', 1, 'e1')
        cache.set(key, 'v1')
        cache.set('plain', 'v2')

        with pytest.raises(dc.IndexKeyError):
            cache.create_composite_index()
        assert cache.composite_index == 0

        del cache['plain']
        cache.create_composite_index()
        assert cache.composite_index == 1
        assert cache.select('binlog') == [(key, 'v1')]

        cache.drop_composite_index()
        assert cache.composite_index == 0
        assert cache._sql(
            'SELECT COUNT(*) FROM Cache WHERE index_source IS NOT NULL'
        ).fetchone()[0] == 0
        with pytest.raises(dc.IndexKeyError):
            cache.select('binlog')
    shutil.rmtree(cache.directory, ignore_errors=True)


def test_enable_on_existing_directory_reports_unindexable():
    directory = tempfile.mkdtemp()
    try:
        # Existing directory populated with the index disabled.
        with dc.Cache(directory) as cache:
            cache.set(make_key('binlog', 1, 'e1'), 'v1')
            cache.set('plain', 'v2')

        with pytest.raises(dc.IndexKeyError) as excinfo:
            dc.Cache(directory, composite_index=True)

        message = str(excinfo.value)
        assert 'plain' in message
        assert '1 existing entry cannot be indexed' in message

        # Reopening without the flag must not keep failing and data is intact.
        with dc.Cache(directory) as cache:
            assert cache.composite_index == 0
            assert len(cache) == 2
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_enable_on_existing_directory_backfills_indexable():
    directory = tempfile.mkdtemp()
    try:
        with dc.Cache(directory) as cache:
            cache.set(make_key('binlog', 1, 'e1'), 'v1')
            cache.set(make_key('binlog', 2, 'e2'), 'v2')

        with dc.Cache(directory, composite_index=True) as cache:
            assert [
                value for _, value in cache.select('binlog')
            ] == ['v1', 'v2']
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_legacy_schema_migrates_and_disabled_by_default():
    directory = tempfile.mkdtemp()
    try:
        connection = sqlite3.connect(op.join(directory, 'cache.db'))
        connection.execute(
            'CREATE TABLE Settings (key TEXT NOT NULL UNIQUE, value)'
        )
        for name, value in dc.DEFAULT_SETTINGS.items():
            connection.execute(
                'INSERT INTO Settings VALUES (?, ?)', (name, value)
            )
        for name, value in METADATA.items():
            connection.execute(
                'INSERT INTO Settings VALUES (?, ?)', (name, value)
            )
        connection.execute(
            'CREATE TABLE Cache ('
            ' rowid INTEGER PRIMARY KEY, key BLOB, raw INTEGER,'
            ' store_time REAL, expire_time REAL, access_time REAL,'
            ' access_count INTEGER DEFAULT 0, tag BLOB,'
            ' size INTEGER DEFAULT 0, mode INTEGER DEFAULT 0,'
            ' filename TEXT, value BLOB)'
        )
        connection.execute(
            'CREATE UNIQUE INDEX Cache_key_raw ON Cache(key, raw)'
        )
        connection.commit()
        connection.close()

        with dc.Cache(directory) as cache:
            columns = {
                row[1]
                for row in cache._sql('PRAGMA table_info(Cache)').fetchall()
            }
            assert 'index_source' in columns
            assert 'index_business_time' in columns
            assert cache.composite_index == 0
            cache.set(make_key('binlog', 1, 'e1'), 'v1')
            assert len(cache.check()) == 0
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_index_persists_across_reopens(cache):
    seed(cache)
    directory = cache.directory
    cache.close()

    with dc.Cache(directory) as reopened:
        assert reopened.composite_index == 1
        assert [
            key['event_id'] for key, _ in reopened.select('binlog')
        ] == ['e1', 'e4', 'e2', 'e3']


def test_fanout_select_merges_shards():
    with dc.FanoutCache(shards=4, composite_index=True) as fanout:
        for num in range(20):
            fanout.set(
                make_key('binlog', num % 5, 'event-%02d' % num), num
            )

        results = fanout.select('binlog', start=1, end=3)
        business_times = [key['business_time'] for key, _ in results]
        assert business_times == sorted(business_times)
        assert all(1 <= business_time <= 3 for business_time in business_times)
        assert len(results) == 12

        limited = fanout.select('binlog', limit=3)
        assert len(limited) == 3
        assert [key['business_time'] for key, _ in limited] == sorted(
            key['business_time'] for key, _ in limited
        )
    shutil.rmtree(fanout.directory, ignore_errors=True)


def test_fanout_requires_enabled_index():
    with dc.FanoutCache(shards=2) as fanout:
        with pytest.raises(dc.IndexKeyError):
            fanout.select('binlog')
    shutil.rmtree(fanout.directory, ignore_errors=True)


def test_fanout_select_orders_null_business_time_first():
    with dc.FanoutCache(shards=4, composite_index=True) as fanout:
        for event_id in ('n1', 'n2'):
            fanout.set(make_key(None, None, event_id), event_id)
        fanout.set(make_key(None, 5, 'a'), 'a')
        fanout.set(make_key(None, 6, 'b'), 'b')

        results = fanout.select(None)
        businesses = [key['business_time'] for key, _ in results]
        assert businesses[:2] == [None, None]
        assert businesses[2:] == [5, 6]
        assert sorted(value for _, value in results[:2]) == ['n1', 'n2']

        # A repeated call returns the same order; limit truncates it.
        assert fanout.select(None) == results
        assert fanout.select(None, limit=1) == results[:1]
    shutil.rmtree(fanout.directory, ignore_errors=True)
