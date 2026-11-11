"""Versioned value codecs and explicit codec migration.

This module adds an explicit, versioned serialization path to diskcache
without changing the legacy on-disk format:

* Legacy entries keep working. A blob without the frame magic is decoded using
  the registered legacy codec (pickle by default).
* New writes are wrapped in a self-describing frame carrying the codec
  ``format`` name and a format ``version`` so entries remain identifiable.
* Unknown formats or versions and decode failures raise
  :class:`CodecError` subclasses. They are never silently rewritten as empty
  or default values.

Use :class:`VersionedDisk` as the ``disk`` argument of :class:`diskcache.Cache`
and :meth:`diskcache.Cache.migrate_codec` to preview or perform migration.
"""

import io
import json
import math
import os.path as op
import pickle
import sqlite3
import struct

from .core import Disk, MODE_PICKLE, UNKNOWN

FRAME_MAGIC = b'DCFC'
FRAME_HEADER_VERSION = 1
FRAME_PREFIX = FRAME_MAGIC + struct.pack('!B', FRAME_HEADER_VERSION)


class CodecError(Exception):
    """Base class for codec registration, encoding, and decoding errors.

    Errors carry the offending cache ``key`` (when known), the codec
    ``format`` name, the format ``version``, and a human readable ``reason``.
    """

    def __init__(self, key=None, format=None, version=None, reason=None):
        self.key = key
        self.format = format
        self.version = version
        self.reason = reason
        parts = [self.__class__.__name__]
        if key is not None:
            parts.append('key=%r' % (key,))
        if format is not None:
            parts.append('format=%r' % (format,))
        if version is not None:
            parts.append('version=%r' % (version,))
        if reason is not None:
            parts.append('reason=%s' % reason)
        super().__init__(': '.join(parts))

    def as_dict(self):
        """Return the error details as a plain dictionary."""
        return {
            'key': self.key,
            'format': self.format,
            'version': self.version,
            'reason': self.reason,
        }


class UnknownFormatError(CodecError):
    """Raised when a frame references an unregistered format or version."""


class UnrepresentableError(CodecError):
    """Raised when a value cannot be encoded by the target codec."""


class DecodeError(CodecError):
    """Raised when a registered codec fails to decode a payload."""


class MigrationReport:
    """Summary of a codec migration preview or run.

    Attributes:
        scanned: rows examined
        migrated: rows rewritten (or that would be rewritten in a dry run)
        skipped: rows already in the target format or stored natively
        failures: dictionaries describing entries that could not be migrated;
            the offending entries are left untouched
    """

    def __init__(self):
        self.scanned = 0
        self.migrated = 0
        self.skipped = 0
        self.failures = []

    def add_failure(self, rowid, key, stage, exc):
        """Record an entry that could not be represented or decoded."""
        details = exc.as_dict() if isinstance(exc, CodecError) else {
            'key': key,
            'format': None,
            'version': None,
            'reason': str(exc),
        }
        details.update({'rowid': rowid, 'stage': stage})
        if details.get('key') is None:
            details['key'] = key
        self.failures.append(details)

    def as_dict(self):
        """Return the report as a plain dictionary."""
        return {
            'scanned': self.scanned,
            'migrated': self.migrated,
            'skipped': self.skipped,
            'failures': list(self.failures),
        }

    def __repr__(self):
        return (
            'MigrationReport(scanned=%d, migrated=%d, skipped=%d,'
            ' failures=%d)'
            % (self.scanned, self.migrated, self.skipped, len(self.failures))
        )


def encode_frame(format, version, payload):
    """Wrap a codec payload in a self-describing versioned frame.

    Frame layout::

        MAGIC (4 bytes) | header version (1 byte) | format length (1 byte)
        | format (bytes) | payload version (2 bytes, big endian) | payload

    :param str format: codec format name
    :param int version: codec format version
    :param bytes payload: encoded value
    :return: framed bytes
    """
    format_bytes = format.encode('utf-8')
    if len(format_bytes) > 255:
        raise ValueError('codec format name too long: %r' % format)
    return b''.join(
        (
            FRAME_PREFIX,
            struct.pack('!B', len(format_bytes)),
            format_bytes,
            struct.pack('!H', version),
            payload,
        )
    )


def decode_frame(blob):
    """Parse a frame produced by :func:`encode_frame`.

    :param bytes blob: framed bytes
    :return: ``(format, version, payload)`` tuple
    :raises UnknownFormatError: when the blob is not a supported frame
    """
    if not blob.startswith(FRAME_PREFIX):
        raise UnknownFormatError(reason='missing or unsupported frame magic')
    offset = len(FRAME_PREFIX)
    try:
        (format_length,) = struct.unpack_from('!B', blob, offset)
        offset += 1
        format = blob[offset : offset + format_length].decode('utf-8')
        offset += format_length
        (version,) = struct.unpack_from('!H', blob, offset)
        offset += 2
    except (struct.error, UnicodeDecodeError) as exc:
        raise UnknownFormatError(reason='malformed frame header') from exc
    return format, version, blob[offset:]


def is_framed(blob):
    """Return True when `blob` carries the versioned frame magic."""
    return blob.startswith(FRAME_PREFIX)


class Codec:
    """Base class for value codecs."""

    format = None
    VERSION = 1

    def encode(self, value):
        """Encode `value` into bytes.

        :raises UnrepresentableError: when the value cannot be represented
        """
        raise NotImplementedError

    def decode(self, payload):
        """Decode a Python value from `payload` bytes."""
        raise NotImplementedError


class JSONCodec(Codec):
    """Strict JSON codec for business-level structured values.

    Only structured JSON types are accepted by default: ``dict``, ``list``,
    ``str``, ``int``, ``float``, ``bool`` and ``None``. Values outside the
    allowed types, such as custom objects, tuples, or sets, raise
    :class:`UnrepresentableError` instead of being silently coerced.
    """

    format = 'json'
    VERSION = 1

    DEFAULT_ALLOWED_TYPES = (dict, list, str, int, float, bool, type(None))

    def __init__(self, allowed_types=None):
        self.allowed_types = (
            self.DEFAULT_ALLOWED_TYPES
            if allowed_types is None
            else tuple(allowed_types)
        )

    def encode(self, value):
        self._validate(value, '$')
        try:
            return json.dumps(value, ensure_ascii=False).encode('utf-8')
        except (TypeError, ValueError) as exc:
            raise UnrepresentableError(
                format=self.format,
                version=self.VERSION,
                reason=str(exc),
            ) from exc

    def _validate(self, value, path):
        if not isinstance(value, self.allowed_types):
            raise UnrepresentableError(
                format=self.format,
                version=self.VERSION,
                reason='type %s is not an allowed structured type at %s'
                % (type(value).__name__, path),
            )
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str):
                    raise UnrepresentableError(
                        format=self.format,
                        version=self.VERSION,
                        reason='non-string dict key %r at %s' % (key, path),
                    )
                self._validate(item, '%s[%r]' % (path, key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                self._validate(item, '%s[%d]' % (path, index))
        elif isinstance(value, float) and not math.isfinite(value):
            raise UnrepresentableError(
                format=self.format,
                version=self.VERSION,
                reason='non-finite float %r at %s' % (value, path),
            )

    def decode(self, payload):
        try:
            return json.loads(payload.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DecodeError(
                format=self.format,
                version=self.VERSION,
                reason=str(exc),
            ) from exc


class PickleCodec(Codec):
    """Pickle codec used to read legacy (pre-framing) entries."""

    format = 'pickle'
    VERSION = 1

    def __init__(self, protocol=pickle.HIGHEST_PROTOCOL):
        self.protocol = protocol

    def encode(self, value):
        return pickle.dumps(value, protocol=self.protocol)

    def decode(self, payload):
        try:
            return pickle.load(io.BytesIO(payload))
        except Exception as exc:
            raise DecodeError(
                format=self.format,
                version=self.VERSION,
                reason=str(exc),
            ) from exc


class CodecRegistry:
    """Registry mapping ``(format, version)`` pairs to codecs."""

    def __init__(self):
        self._codecs = {}
        self._legacy = []

    def register(self, codec, version=None, legacy=False):
        """Register a codec, optionally marking it as a legacy format."""
        version = codec.VERSION if version is None else version
        identity = (codec.format, version)
        self._codecs[identity] = codec
        if legacy and identity not in self._legacy:
            self._legacy.append(identity)
        return codec

    def get(self, format, version):
        """Return the registered codec or raise :class:`UnknownFormatError`."""
        try:
            return self._codecs[(format, version)]
        except KeyError:
            raise UnknownFormatError(
                format=format,
                version=version,
                reason='no codec registered for format and version',
            ) from None

    def is_legacy(self, format, version):
        """Return True when the format and version pair is legacy."""
        return (format, version) in self._legacy

    def legacy_codec(self):
        """Return the codec used for blobs without a frame magic."""
        if not self._legacy:
            raise UnknownFormatError(reason='no legacy codec registered')
        return self.get(*self._legacy[0])

    @classmethod
    def default(cls, primary):
        """Build a registry with pickle as legacy format and `primary`."""
        registry = cls()
        registry.register(PickleCodec(), legacy=True)
        registry.register(primary)
        return registry


class VersionedDisk(Disk):
    """Disk storing complex values through versioned codec frames.

    Native storage paths (raw scalars, bytes, text, and file-like values)
    behave exactly like :class:`diskcache.Disk`. Every other value is encoded
    with the primary codec and wrapped in a frame written through the
    ``MODE_PICKLE`` storage path. On read, framed blobs are dispatched to the
    registered codec while unframed blobs are decoded by the registered
    legacy codec.
    """

    def __init__(
        self,
        directory,
        codec=None,
        registry=None,
        min_file_size=0,
        pickle_protocol=0,
    ):
        super().__init__(directory, min_file_size, pickle_protocol)
        self.codec = JSONCodec() if codec is None else codec
        self.registry = (
            CodecRegistry.default(self.codec)
            if registry is None
            else registry
        )

    def store(self, value, read, key=UNKNOWN):
        # pylint: disable=unidiomatic-typecheck
        type_value = type(value)

        if read or type_value in (str, bytes, int, float):
            return super().store(value, read, key=key)

        try:
            payload = self.codec.encode(value)
        except CodecError as exc:
            exc.key = key
            raise
        frame = encode_frame(self.codec.format, self.codec.VERSION, payload)
        min_file_size = self.min_file_size

        if len(frame) < min_file_size:
            return 0, MODE_PICKLE, None, sqlite3.Binary(frame)

        filename, full_path = self.filename(key, value)
        self._write(full_path, io.BytesIO(frame), 'xb')
        return len(frame), MODE_PICKLE, filename, None

    def fetch(self, mode, filename, value, read):
        if mode != MODE_PICKLE:
            return super().fetch(mode, filename, value, read)

        if value is None:
            with open(op.join(self._directory, filename), 'rb') as reader:
                blob = reader.read()
        else:
            blob = bytes(value)

        if not is_framed(blob):
            return self.registry.legacy_codec().decode(blob)

        format, version, payload = decode_frame(blob)
        codec = self.registry.get(format, version)
        return codec.decode(payload)
