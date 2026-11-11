``cacheutils`` - Caches and caching
===================================

.. automodule:: boltons.cacheutils


Least-Recently Inserted (LRI)
-----------------------------

The :class:`LRI` is the simpler cache, implementing a very simple first-in,
first-out (FIFO) approach to cache eviction. If the use case calls for
simple, very-low overhead caching, such as somewhat expensive local
operations (e.g., string operations), then the LRI is likely the right
choice.

.. autoclass:: boltons.cacheutils.LRI
   :members:

Least-Recently Used (LRU)
-------------------------

The :class:`LRU` is the more advanced cache, but it's still quite
simple. When it reaches capacity, a new insertion replaces the
least-recently used item. This strategy makes the LRU a more effective
cache than the LRI for a wide variety of applications, but also
entails more operations for all of its APIs, especially reads. Unlike
the :class:`LRI`, the LRU has threadsafety built in.

.. autoclass:: boltons.cacheutils.LRU
   :members:

Automatic function caching
--------------------------

Continuing in the theme of cache tunability and experimentation,
``cacheutils`` also offers a pluggable way to cache function return
values: the :func:`cached` function decorator and the
:func:`cachedmethod` method decorator.

.. autofunction:: boltons.cacheutils.cached
.. autofunction:: boltons.cacheutils.cachedmethod

Similar functionality can be found in Python 3.4's
:func:`functools.lru_cache` decorator, but the functools approach does
not support the same cache strategy modification, nor does it support
sharing the cache object across multiple functions.

.. autofunction:: boltons.cacheutils.cachedproperty

Signature-normalized cache keys
-------------------------------

The default :func:`make_cache_key` treats positional and keyword
arguments as structurally different, so ``f(1)`` and ``f(x=1)`` produce
different cache entries. Pass a ``converters`` mapping to
:func:`cached` or :func:`cachedmethod` to opt into
:class:`SignatureCacheKeyBuilder`, which binds each call through the
wrapped function's :func:`inspect.signature` first. The same logical
call then shares one memoized result whether an argument is passed
positionally, by keyword, or omitted in favor of its default.

The mapping's keys are types and its values are callables declaring how
values of that exact type should be expressed as stable, hashable key
data. This is the supported way to cache calls taking :class:`list`,
:class:`dict`, and other mutable or unhashable values:

.. code-block:: python

    from boltons.cacheutils import LRU, cached

    cache = LRU()

    @cached(cache, converters={
        list: lambda value: tuple(value),
        dict: lambda value: tuple(sorted(value.items())),
    })
    def clean(items, options=None, strict=True):
        ...

    clean([1, 2])
    clean(items=[1, 2])              # same cache entry
    clean([1, 2], None, True)        # same cache entry
    clean([1, 2], strict=False)      # distinct entry

Containers are also normalized structurally without a converter, with
their kind tagged so a :class:`list` and :class:`tuple` with equal
contents never collide. Values that are unhashable, or only
distinguishable by object identity (a plain instance whose hash is
effectively its memory address), raise
:class:`UncacheableArgumentError`. In that case the wrapped function
still executes normally; the call is simply never read from or written
to the cache. No ``repr()`` or object identity is ever used to fabricate
a key. Register a converter to declare a stable representation instead.

:func:`cachedmethod` applies the same rules, treating the bound instance
as an explicit per-instance cache scope, so bound (``obj.m(x)``) and
unbound (``C.m(obj, x)``) calls share an entry while distinct instances
stay isolated. Variadic parameters (``*args``/``**kwargs``) keep their
structural markers, so one tuple argument can never be confused with
multiple positional arguments.

.. autoclass:: boltons.cacheutils.SignatureCacheKeyBuilder
   :members:

.. autoexception:: boltons.cacheutils.UncacheableArgumentError

.. autofunction:: boltons.cacheutils.make_signature_cache_key

Threshold-bounded Counting
--------------------------

.. autoclass:: boltons.cacheutils.ThresholdCounter
   :members:
