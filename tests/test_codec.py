"""Tests for versioned codecs and explicit codec migration."""

import os.path as op
import pickle
import pickletools
import shutil
import sqlite3

import pytest

import diskcache as dc
from diskcache.codec import (
    CodecRegistry,
    DecodeError,
    JSONCodec,
    PickleCodec,
    UnknownFormatError,
    UnrepresentableError,
    VersionedDisk,
    decode_frame,
    encode_frame,
    is_framed,
)
from diskcache.core import MODE_PICKLE


def db_key(key):
    """Build the optimized pickle blob diskcache stores for non-raw keys."""
    return sqlite3.Binary(pickletools.optimize(pickle.dumps(key)))


def insert_value_row(cache, key, blob, mode=MODE_PICKLE):
    """Insert a raw value blob directly into the Cache table."""
    cache._sql(
        'INSERT INTO Cache(key, raw, store_time, expire_time, access_time,'
        ' access_count, tag, size, mode, filename, value)'
        ' VALUES (?, 0, 0.0, NULL, 0.0, 0, NULL, 0, ?, NULL, ?)',
        (db_key(key), mode, sqlite3.Binary(blob)),
    )


def test_frame_roundtrip():
    frame = encode_frame('json', 1, b'{"a": 1}')
    assert is_framed(frame)
    assert frame.startswith(b'DCFC')
    format, version, payload = decode_frame(frame)
    assert (format, version, payload) == ('json', 1, b'{"a": 1}')


def test_frame_unknown_magic_and_malformed():
    with pytest.raises(UnknownFormatError):
        decode_frame(b'not a frame')
    with pytest.raises(UnknownFormatError):
        decode_frame(b'DCFC\x01\xff')


def test_json_codec_allowed_types():
    codec = JSONCodec()
    values = [
        {'a': 1},
        [1, 'two', 3.0, True, None],
        'text',
        42,
        3.14,
        True,
        None,
    ]
    for value in values:
        assert codec.decode(codec.encode(value)) == value


def test_json_codec_rejects_unrepresentable():
    codec = JSONCodec()
    for value in [
        (1, 2),
        {1, 2},
        object(),
        [1, (2, 3)],
        {'x': {1, 2}},
        {1: 'non-string-key'},
        float('nan'),
        [float('inf')],
    ]:
        with pytest.raises(UnrepresentableError):
            codec.encode(value)


def test_json_codec_decode_error():
    with pytest.raises(DecodeError):
        JSONCodec().decode(b'not json')


def test_registry_lookup_and_legacy():
    registry = CodecRegistry()
    pickle_codec = PickleCodec()
    json_codec = JSONCodec()
    registry.register(pickle_codec, legacy=True)
    registry.register(json_codec)

    assert registry.get('pickle', 1) is pickle_codec
    assert registry.legacy_codec() is pickle_codec
    assert registry.is_legacy('pickle', 1)
    with pytest.raises(UnknownFormatError):
        registry.get('json', 99)


def test_versioned_disk_native_storage_modes_unchanged(tmp_path):
    with dc.Cache(tmp_path, disk=VersionedDisk) as cache:
        cache['int'] = 5
        cache['float'] = 1.5
        cache['bytes'] = b'raw-bytes'
        cache['text'] = 'x' * 100
        rows = cache._sql(
            'SELECT mode FROM Cache ORDER BY rowid'
        ).fetchall()
        modes = {row[0] for row in rows}
        assert MODE_PICKLE not in modes
        assert cache['int'] == 5
        assert cache['bytes'] == b'raw-bytes'
        assert cache['text'] == 'x' * 100


def test_versioned_disk_frames_structured_values(tmp_path):
    with dc.Cache(tmp_path, disk=VersionedDisk) as cache:
        value = {'order': 1, 'items': ['a', 'b'], 'ok': True}
        cache['structured'] = value
        ((mode, blob),) = cache._sql(
            'SELECT mode, value FROM Cache WHERE mode = ?',
            (MODE_PICKLE,),
        ).fetchall()
        assert is_framed(bytes(blob))
        assert cache['structured'] == value


def test_versioned_disk_file_backed_frame(tmp_path):
    with dc.Cache(
        tmp_path, disk=VersionedDisk, disk_min_file_size=32
    ) as cache:
        value = {'pad': 'z' * 200}
        cache['big'] = value
        ((filename,),) = cache._sql(
            'SELECT filename FROM Cache WHERE filename IS NOT NULL'
        ).fetchall()
        full_path = op.join(str(tmp_path), filename)
        with open(full_path, 'rb') as reader:
            assert is_framed(reader.read())
        assert cache['big'] == value


def test_unrepresentable_write_leaves_no_entry(tmp_path):
    with dc.Cache(tmp_path, disk=VersionedDisk) as cache:
        with pytest.raises(UnrepresentableError):
            cache.set('bad', {1, 2})
        assert 'bad' not in cache
        assert cache.get('bad', default='sentinel') == 'sentinel'


def test_legacy_unframed_pickle_still_reads(tmp_path):
    with dc.Cache(tmp_path) as legacy:
        legacy[('key',)] = {'tuple': (1, 2, 3)}

    with dc.Cache(tmp_path, disk=VersionedDisk) as cache:
        assert cache[('key',)] == {'tuple': (1, 2, 3)}


def test_unknown_version_is_not_rewritten_as_default(tmp_path):
    with dc.Cache(tmp_path, disk=VersionedDisk) as cache:
        insert_value_row(
            cache, ('future',), encode_frame('json', 99, b'{}')
        )
        with pytest.raises(UnknownFormatError):
            cache.get(('future',), default='must-not-return')
        with pytest.raises(UnknownFormatError):
            cache[('future',)]


def test_corrupt_legacy_blob_raises_decode_error(tmp_path):
    with dc.Cache(tmp_path, disk=VersionedDisk) as cache:
        insert_value_row(cache, ('broken',), b'\x80\x04garbage')
        with pytest.raises(DecodeError):
            cache[('broken',)]


def test_migrate_requires_versioned_disk(tmp_path):
    with dc.Cache(tmp_path) as cache:
        with pytest.raises(TypeError):
            cache.migrate_codec()


def test_migrate_dry_run_previews_without_writing(tmp_path):
    with dc.Cache(tmp_path) as legacy:
        legacy['good'] = {'a': 1}
        legacy[('tuple',)] = (1, 2)
        legacy['scalar'] = 5

    with dc.Cache(tmp_path, disk=VersionedDisk) as cache:
        before = cache._sql(
            'SELECT rowid, mode, value, filename FROM Cache ORDER BY rowid'
        ).fetchall()

        report = cache.migrate_codec(dry_run=True)

        after = cache._sql(
            'SELECT rowid, mode, value, filename FROM Cache ORDER BY rowid'
        ).fetchall()
        assert before == after

        assert report.scanned == 3
        assert report.migrated == 1
        assert report.skipped == 1
        assert len(report.failures) == 1
        failure = report.failures[0]
        assert failure['stage'] == 'encode'
        assert failure['key'] == ('tuple',)
        assert 'tuple' in failure['reason']


def test_migrate_rewrites_entries_and_keeps_failures_readable(tmp_path):
    with dc.Cache(tmp_path) as legacy:
        legacy['good'] = {'a': 1}
        legacy[('tuple',)] = (1, 2)
        legacy[('set',)] = {'x': {1}}

    with dc.Cache(tmp_path, disk=VersionedDisk) as cache:
        report = cache.migrate_codec(dry_run=False)
        assert report.migrated == 1
        assert len(report.failures) == 2
        assert {f['key'] for f in report.failures} == {
            ('tuple',),
            ('set',),
        }

        rows = cache._sql(
            'SELECT key, mode, value FROM Cache WHERE mode = ?',
            (MODE_PICKLE,),
        ).fetchall()
        framed = [
            bytes(value)
            for _, mode, value in rows
            if value is not None and is_framed(bytes(value))
        ]
        assert len(framed) == 1

        assert cache['good'] == {'a': 1}
        # Failed entries remain on disk and keep their legacy representation.
        assert cache[('tuple',)] == (1, 2)
        assert cache[('set',)] == {'x': {1}}

        rerun = cache.migrate_codec(dry_run=False)
        assert rerun.migrated == 0
        assert len(rerun.failures) == 2


def test_migrate_decode_failure_leaves_entry_untouched(tmp_path):
    with dc.Cache(tmp_path, disk=VersionedDisk) as cache:
        insert_value_row(cache, ('broken',), b'\x80\x04garbage')
        report = cache.migrate_codec(dry_run=False)
        assert report.migrated == 0
        assert len(report.failures) == 1
        assert report.failures[0]['stage'] == 'decode'
        ((blob,),) = cache._sql(
            'SELECT value FROM Cache'
        ).fetchall()
        assert bytes(blob) == b'\x80\x04garbage'


def test_migrate_with_backup_supports_rollback(tmp_path):
    backup = str(tmp_path) + '-backup'
    with dc.Cache(tmp_path) as legacy:
        legacy['good'] = {'a': 1}
        legacy[('tuple',)] = (1, 2)

    with dc.Cache(tmp_path, disk=VersionedDisk) as cache:
        report = cache.migrate_codec(
            dry_run=False, backup_dir=backup
        )
        assert report.migrated == 1
        assert op.exists(op.join(backup, 'cache.db'))

    original = str(tmp_path) + '-original'
    shutil.copytree(tmp_path, original)
    shutil.rmtree(tmp_path)
    shutil.copytree(backup, tmp_path)

    with dc.Cache(tmp_path) as restored:
        assert restored['good'] == {'a': 1}
        assert restored[('tuple',)] == (1, 2)

    shutil.rmtree(original, ignore_errors=True)


class JSONCodecV2(JSONCodec):
    format = 'json'
    VERSION = 2


class CodecV2Disk(VersionedDisk):
    def __init__(self, directory, **kwargs):
        registry = CodecRegistry()
        registry.register(PickleCodec(), legacy=True)
        registry.register(JSONCodec())
        registry.register(JSONCodecV2())
        super().__init__(
            directory,
            codec=JSONCodecV2(),
            registry=registry,
            **kwargs,
        )


def test_registered_older_format_reads_and_migrates_to_newer(tmp_path):
    with dc.Cache(tmp_path, disk=CodecV2Disk) as cache:
        insert_value_row(
            cache, ('old',), encode_frame('json', 1, b'{"v": 1}')
        )
        assert cache[('old',)] == {'v': 1}

        report = cache.migrate_codec(dry_run=False)
        assert report.migrated == 1
        ((blob,),) = cache._sql(
            'SELECT value FROM Cache'
        ).fetchall()
        format, version, _ = decode_frame(bytes(blob))
        assert (format, version) == ('json', 2)
        assert cache[('old',)] == {'v': 1}


def test_fanout_migrate_and_backup(tmp_path):
    directory = str(tmp_path / 'fan')
    with dc.FanoutCache(directory, shards=4) as fan:
        for number in range(12):
            fan['k%d' % number] = {'i': number}

    with dc.FanoutCache(
        directory, shards=4, disk=VersionedDisk
    ) as fan:
        preview = fan.migrate_codec(dry_run=True)
        assert preview.scanned == 12
        assert preview.migrated == 12

        backup = str(tmp_path / 'fan-backup')
        report = fan.migrate_codec(
            dry_run=False, backup_dir=backup
        )
        assert report.migrated == 12
        assert op.exists(op.join(backup, '000', 'cache.db'))
        assert fan['k0'] == {'i': 0}

        rerun = fan.migrate_codec(dry_run=False)
        assert rerun.migrated == 0
        assert rerun.skipped == 12
