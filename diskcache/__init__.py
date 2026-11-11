"""
DiskCache API Reference
=======================

The :doc:`tutorial` provides a helpful walkthrough of most methods.
"""

from .codec import (
    Codec,
    CodecError,
    CodecRegistry,
    DecodeError,
    JSONCodec,
    MigrationReport,
    PickleCodec,
    UnknownFormatError,
    UnrepresentableError,
    VersionedDisk,
)
from .core import (
    DEFAULT_SETTINGS,
    ENOVAL,
    EVICTION_POLICY,
    UNKNOWN,
    Cache,
    Disk,
    EmptyDirWarning,
    JSONDisk,
    Timeout,
    UnknownFileWarning,
)
from .fanout import FanoutCache
from .persistent import Deque, Index
from .recipes import (
    Averager,
    BoundedSemaphore,
    Lock,
    RLock,
    barrier,
    memoize_stampede,
    throttle,
)

__all__ = [
    'Averager',
    'BoundedSemaphore',
    'Cache',
    'Codec',
    'CodecError',
    'CodecRegistry',
    'DEFAULT_SETTINGS',
    'DecodeError',
    'Deque',
    'Disk',
    'ENOVAL',
    'EVICTION_POLICY',
    'EmptyDirWarning',
    'FanoutCache',
    'Index',
    'JSONCodec',
    'JSONDisk',
    'Lock',
    'MigrationReport',
    'PickleCodec',
    'RLock',
    'Timeout',
    'UNKNOWN',
    'UnknownFileWarning',
    'UnknownFormatError',
    'UnrepresentableError',
    'VersionedDisk',
    'barrier',
    'memoize_stampede',
    'throttle',
]

try:
    from .djangocache import DjangoCache  # noqa

    __all__.append('DjangoCache')
except Exception:  # pylint: disable=broad-except  # pragma: no cover
    # Django not installed or not setup so ignore.
    pass

__title__ = 'diskcache'
__version__ = '5.6.3'
__build__ = 0x050603
__author__ = 'Grant Jenks'
__license__ = 'Apache 2.0'
__copyright__ = 'Copyright 2016-2023 Grant Jenks'
