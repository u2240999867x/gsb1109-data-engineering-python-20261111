Codec Migration
===============

Diskcache stores complex values with pickle by default. To restrict the cache
to business-approved structured types and to make the stored format
identifiable, use :class:`diskcache.VersionedDisk`.

.. code-block:: python

    from diskcache import Cache, VersionedDisk

    cache = Cache('data.cache', disk=VersionedDisk)

    cache['order'] = {'id': 123, 'items': ['a', 'b'], 'paid': True}

The default codec is a strict :class:`diskcache.JSONCodec` which accepts
``dict``, ``list``, ``str``, ``int``, ``float``, ``bool``, and ``None``,
recursively. Non-string dictionary keys, tuples, sets, custom objects, and
non-finite floats are rejected with
:class:`diskcache.UnrepresentableError`. The rejected value is not written.

Framed values
-------------

New values written by ``VersionedDisk`` carry a self-describing frame with a
magic, the codec format name, and a format version. The frame is stored
through the same database or file paths that diskcache already uses for
pickled values, so the rest of the cache API is unchanged.

Legacy entries
--------------

Values written before the migration do not have the frame magic. They are
decoded with the registered legacy codec, which is pickle by default. Old
entries therefore remain readable after switching a cache to
``VersionedDisk``.

When a frame names a format or version that is not registered, or when a
registered codec cannot decode the payload, a
:class:`diskcache.UnknownFormatError` or :class:`diskcache.DecodeError` is
raised. These errors are not swallowed: a missing or corrupt value is never
silently replaced with an empty or default value.

Previewing and running migration
--------------------------------

Run a dry run first. It reports which entries cannot be represented by the
target codec and does not modify the cache.

.. code-block:: python

    report = cache.migrate_codec(dry_run=True)
    print(report.scanned, report.migrated, report.skipped)
    for failure in report.failures:
        print(failure['key'], failure['stage'], failure['reason'])

Only when the preview looks correct should the real migration be run. Passing
``backup_dir`` copies the original cache first, so it can be restored if
needed.

.. code-block:: python

    report = cache.migrate_codec(dry_run=False, backup_dir='data.cache.backup')

Migration re-encodes entries in place through the target disk. Native
storage entries (raw scalars, bytes, text, and file-like values) and entries
already in the target format are skipped. Entries that cannot be decoded or
represented are recorded in ``report.failures``, left untouched, and remain
readable through their legacy codec.

The cache is never cleared and the original cache directory is never
rewritten wholesale. To roll back, replace the cache directory with the
backup copy. A backup can also be created explicitly with ``cache.backup()``.

Because migration updates rows in place, run it while other writers are
stopped or expect the cache to remain available but shifting entry by entry.
Framed values are not readable by older diskcache releases that do not know
the frame format, so keep the backup until the migrated cache has been
verified.

Registering formats
-------------------

Multiple codecs can be registered so older framed formats can still be read
while new writes use the primary codec.

.. code-block:: python

    from diskcache import (
        Cache,
        CodecRegistry,
        PickleCodec,
        JSONCodec,
        VersionedDisk,
    )

    class JSONCodecV2(JSONCodec):
        format = 'json'
        VERSION = 2

    class CompanyDisk(VersionedDisk):
        def __init__(self, directory, **kwargs):
            registry = CodecRegistry()
            registry.register(PickleCodec(), legacy=True)
            registry.register(JSONCodec())    # version 1
            registry.register(JSONCodecV2())  # version 2, primary
            super().__init__(
                directory,
                codec=JSONCodecV2(),
                registry=registry,
                **kwargs,
            )

    cache = Cache('data.cache', disk=CompanyDisk)

Pass the same disk subclass whenever the cache is reopened so older framed
versions stay readable. ``Cache.migrate_codec`` re-encodes entries from prior
registered versions to the primary codec's format and version.
``FanoutCache.migrate_codec`` applies the same process to every shard and
merges the reports.
