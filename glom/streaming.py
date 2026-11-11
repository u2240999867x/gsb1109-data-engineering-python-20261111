"""glom's helpers for streaming use cases.

Specifier types which yield their results incrementally so that they
can be applied to targets which are themselves streaming (e.g. chunks
of rows from a database, lines from a file) without excessive memory
usage.

glom's streaming functionality revolves around a single :class:`Iter`
Specifier type, which has methods to transform the target stream.
"""

from collections import deque, namedtuple
from itertools import islice, dropwhile, takewhile, chain
from functools import partial
try:
    from itertools import imap, ifilter
except ImportError:
    # py3
    imap = map
    ifilter = filter

from boltons.iterutils import split_iter, chunked_iter, windowed_iter, unique_iter, first
from boltons.typeutils import make_sentinel
from boltons.funcutils import FunctionBuilder

from .core import (glom, T, STOP, SKIP, _MISSING, Path, TargetRegistry, Call,
                   Spec, Pipe, S, GlomError, bbrepr, format_invocation)
from .matching import Check

# default backpressure policy: stop pulling from the source once an outlet's
# bounded buffer fills up, rather than reading the whole input into memory
BLOCK = make_sentinel('BLOCK')
# alternative backpressure policy: drop the newest (current) record for a
# backed-up outlet; useful when a slow outlet must not stall the others
DROP_NEWEST = make_sentinel('DROP_NEWEST')


class BackpressureFull(GlomError):
    """Raised when a :data:`BLOCK` :class:`Outlet`'s bounded buffer is full
    and a :class:`FanoutRun` is driven (via :meth:`FanoutRun.run` or
    iteration) without first draining it.

    The non-raising :meth:`FanoutRun.run_once` returns :data:`BACKPRESSURE`
    instead. The offending outlet is available as the ``outlet`` attribute,
    and the number of records it had buffered is available as ``size``.
    """
    def __init__(self, outlet, size):
        self.outlet = outlet
        self.size = size

    def get_message(self):
        return ('outlet %r buffer is full (max_size=%r, %r records pending)'
                % (self.outlet.name, self.outlet.max_size, self.size))


class Iter:
    """``Iter()`` is glom's counterpart to Python's built-in :func:`iter()`
    function. Given an iterable target, ``Iter()`` yields the result
    of applying the passed spec to each element of the target, similar
    to the built-in ``[]`` spec, but streaming.

    The following turns a list of strings into integers using Iter(),
    before deduplicating and converting it to a tuple:

    >>> glom(['1', '2', '1', '3'], (Iter(int), set, tuple))
    (1, 2, 3)

    ``Iter()`` also has many useful methods which can be chained to
    compose a stream processing pipeline. The above can also be
    written as:

    >>> glom(['1', '2', '1', '3'], (Iter().map(int).unique(), tuple))
    (1, 2, 3)

    ``Iter()`` also respects glom's :data:`~glom.SKIP` and
    :data:`~glom.STOP` singletons for filtering and breaking
    iteration.

    Args:

       subspec: A subspec to be applied on each element from the iterable.
       sentinel: Keyword-only argument, which, when found in the
         iterable stream, causes the iteration to stop. Same as with the
         built-in :func:`iter`.

    """
    def __init__(self, subspec=T, **kwargs):
        self.subspec = subspec
        self._iter_stack = kwargs.pop('_iter_stack', [])

        self.sentinel = kwargs.pop('sentinel', STOP)
        if kwargs:
            raise TypeError('unexpected keyword arguments: %r' % sorted(kwargs))
        return

    def __repr__(self):
        base_args = ()
        if self.subspec != T:
            base_args = (self.subspec,)
        base = format_invocation(self.__class__.__name__, base_args, repr=bbrepr)
        chunks = [base]
        for fname, args, _ in reversed(self._iter_stack):
            meth = getattr(self, fname)
            fb = FunctionBuilder.from_func(meth)
            fb.args = fb.args[1:]  # drop self
            arg_names = fb.get_arg_names()
            # TODO: something fancier with defaults:
            kwargs = []
            if len(args) > 1 and arg_names:
                args, kwargs = (), zip(arg_names, args)
            chunks.append('.' + format_invocation(fname, args, kwargs, repr=bbrepr))
        return ''.join(chunks)

    def glomit(self, target, scope):
        iterator = self._iterate(target, scope)

        for _, _, callback in reversed(self._iter_stack):
            iterator = callback(iterator, scope)

        return iter(iterator)

    def _iterate(self, target, scope):
        iterate = scope[TargetRegistry].get_handler('iterate', target, path=scope[Path])
        try:
            iterator = iterate(target)
        except Exception as e:
            raise TypeError('failed to iterate on instance of type %r at %r (got %r)'
                            % (target.__class__.__name__, Path(*scope[Path]), e))

        base_path = scope[Path]
        for i, t in enumerate(iterator):
            scope[Path] = base_path + [i]
            yld = (t if self.subspec is T else scope[glom](t, self.subspec, scope))
            if yld is SKIP:
                continue
            elif yld is self.sentinel or yld is STOP:
                # NB: sentinel defaults to STOP so I was torn whether
                # to also check for STOP, and landed on the side of
                # never letting STOP through.
                return
            yield yld
        return

    def _add_op(self, opname, args, callback):
        return type(self)(subspec=self.subspec, _iter_stack=[(opname, args, callback)] + self._iter_stack)

    def map(self, subspec):
        """Return a new :class:`Iter()` spec which will apply the provided
        *subspec* to each element of the iterable.

        >>> glom(range(5), Iter().map(lambda x: x * 2).all())
        [0, 2, 4, 6, 8]

        Because a spec can be a callable, :meth:`Iter.map()` does
        everything the built-in :func:`map` does, but with the full
        power of glom specs.

        >>> glom(['a', 'B', 'C'], Iter().map(T.islower()).all())
        [True, False, False]
        """
        # whatever validation you want goes here
        # TODO: DRY the self._add_op with a decorator?
        return self._add_op(
            'map',
            (subspec,),
            lambda iterable, scope: imap(
                lambda t: scope[glom](t, subspec, scope), iterable))

    def filter(self, key=T):
        """Return a new :class:`Iter()` spec which will include only elements matching the
        given *key*.

        >>> glom(range(6), Iter().filter(lambda x: x % 2).all())
        [1, 3, 5]

        Because a spec can be a callable, :meth:`Iter.filter()` does
        everything the built-in :func:`filter` does, but with the full
        power of glom specs. For even more power, combine,
        :meth:`Iter.filter()` with :class:`Check()`.

        >>> # PROTIP: Python's ints know how many binary digits they require, using the bit_length method
        >>> glom(range(9), Iter().filter(Check(T.bit_length(), one_of=(2, 4), default=SKIP)).all())
        [2, 3, 8]

        """
        # NB: Check's validate function defaults to bool, and
        # *default* is returned on access errors as well validation
        # errors, so the lambda passed to ifilter below works fine.
        check_spec = key if isinstance(key, Check) else Check(key, default=SKIP)
        return self._add_op(
            'filter',
            (key,),
            lambda iterable, scope: ifilter(
                lambda t: scope[glom](t, check_spec, scope) is not SKIP, iterable))

    def chunked(self, size, fill=_MISSING):
        """Return a new :class:`Iter()` spec which groups elements in the iterable
        into lists of length *size*.

        If the optional *fill* argument is provided, iterables not
        evenly divisible by *size* will be padded out by the *fill*
        constant. Otherwise, the final chunk will be shorter than *size*.

        >>> list(glom(range(10), Iter().chunked(3)))
        [[0, 1, 2], [3, 4, 5], [6, 7, 8], [9]]
        >>> list(glom(range(10), Iter().chunked(3, fill=None)))
        [[0, 1, 2], [3, 4, 5], [6, 7, 8], [9, None, None]]
        """
        kw = {'size': size}
        args = size,
        if fill is not _MISSING:
            kw['fill'] = fill
            args += (fill,)
        return self._add_op(
            'chunked', args, lambda it, scope: chunked_iter(it, **kw))

    def windowed(self, size):
        """Return a new :class:`Iter()` spec which will yield a sliding window of
        adjacent elements in the iterable. Each tuple yielded will be
        of length *size*.

        Useful for getting adjacent pairs and triples.

        >>> list(glom(range(4), Iter().windowed(2)))
        [(0, 1), (1, 2), (2, 3)]
        """
        return self._add_op(
            'windowed', (size,), lambda it, scope: windowed_iter(it, size))

    def split(self, sep=None, maxsplit=None):
        """Return a new :class:`Iter()` spec which will lazily split an iterable based
        on a separator (or list of separators), *sep*. Like
        :meth:`str.split()`, but for all iterables.

        ``split_iter()`` yields lists of non-separator values. A separator will
        never appear in the output.

        >>> target = [1, 2, None, None, 3, None, 4, None]
        >>> list(glom(target, Iter().split()))
        [[1, 2], [3], [4]]

        Note that ``split_iter`` is based on :func:`str.split`, so if
        *sep* is ``None``, ``split()`` **groups** separators. If empty lists
        are desired between two contiguous ``None`` values, simply use
        ``sep=[None]``:

        >>> list(glom(target, Iter().split(sep=[None])))
        [[1, 2], [], [3], [4], []]

        A max number of splits may also be set:

        >>> list(glom(target, Iter().split(maxsplit=2)))
        [[1, 2], [3], [4, None]]

        """
        return self._add_op(
            'split',
            (sep, maxsplit),
            lambda it, scope: split_iter(it, sep=sep, maxsplit=maxsplit))

    def flatten(self):
        """Returns a new :class:`Iter()` instance which combines iterables into a
        single iterable.

        >>> target = [[1, 2], [3, 4], [5]]
        >>> list(glom(target, Iter().flatten()))
        [1, 2, 3, 4, 5]
        """
        return self._add_op(
            'flatten',
            (),
            lambda it, scope: chain.from_iterable(it))

    def unique(self, key=T):
        """Return a new :class:`Iter()` spec which lazily filters out duplicate
        values, i.e., only the first appearance of a value in a stream will
        be yielded.

        >>> target = list('gloMolIcious')
        >>> out = list(glom(target, Iter().unique(T.lower())))
        >>> print(''.join(out))
        gloMIcus
        """
        return self._add_op(
            'unique',
            (key,),
            lambda it, scope: unique_iter(it, key=lambda t: scope[glom](t, key, scope)))


    def slice(self, *args):
        """Returns a new :class:`Iter()` spec which trims iterables in the
        same manner as :func:`itertools.islice`.

        >>> target = [0, 1, 2, 3, 4, 5]
        >>> glom(target, Iter().slice(3).all())
        [0, 1, 2]
        >>> glom(target, Iter().slice(2, 4).all())
        [2, 3]

        This method accepts only positional arguments.
        """
        # TODO: make a kwarg-compatible version of this (islice takes no kwargs)
        # TODO: also support slice syntax Iter()[::]
        try:
            islice([], *args)
        except TypeError:
            raise TypeError(f'invalid slice arguments: {args!r}')
        return self._add_op('slice', args, lambda it, scope: islice(it, *args))

    def limit(self, count):
        """A convenient alias for :meth:`~Iter.slice`, which takes a single
        argument, *count*, the max number of items to yield.
        """
        return self._add_op('limit', (count,), lambda it, scope: islice(it, count))

    def takewhile(self, key=T):
        """Returns a new :class:`Iter()` spec which stops the stream once
        *key* becomes falsy.

        >>> glom([3, 2, 0, 1], Iter().takewhile().all())
        [3, 2]

        :func:`itertools.takewhile` for more details.
        """
        return self._add_op(
            'takewhile',
            (key,),
            lambda it, scope: takewhile(
                lambda t: scope[glom](t, key, scope), it))

    def dropwhile(self, key=T):
        """Returns a new :class:`Iter()` spec which drops stream items until
        *key* becomes falsy.

        >>> glom([0, 0, 3, 2, 0], Iter().dropwhile(lambda t: t < 1).all())
        [3, 2, 0]

        Note that while similar to :meth:`Iter.filter()`, the filter
        only applies to the beginning of the stream. In a way,
        :meth:`Iter.dropwhile` can be thought of as
        :meth:`~str.lstrip()` for streams. See
        :func:`itertools.dropwhile` for more details.

        """

        return self._add_op(
            'dropwhile',
            (key,),
            lambda it, scope: dropwhile(
                lambda t: scope[glom](t, key, scope), it))

    # Terminal methods follow

    def all(self):
        """A convenience method which returns a new spec which turns an
        iterable into a list.

        >>> glom(range(5), Iter(lambda t: t * 2).all())
        [0, 2, 4, 6, 8]

        Note that this spec will always consume the whole iterable, and as
        such, the spec returned is *not* an :class:`Iter()` instance.
        """
        return Pipe(self, list)

    def first(self, key=T, default=None):
        """A convenience method for lazily yielding a single truthy item from
        an iterable.

        >>> target = [False, 1, 2, 3]
        >>> glom(target, Iter().first())
        1

        This method takes a condition, *key*, which can also be a
        glomspec, as well as a *default*, in case nothing matches the
        condition.

        As this spec yields at most one item, and not an iterable, the
        spec returned from this method is not an :class:`Iter()` instance.
        """
        return (self, First(key=key, default=default))


class First:
    """Get the first element of an iterable which matches *key*, if there
    is one, otherwise return *default* (``None`` if unset).

    >>> is_odd = lambda x: x % 2
    >>> glom([0, 1, 2, 3], First(is_odd))
    1
    >>> glom([0, 2, 4], First(is_odd, default=False))
    False
    """
    # The impl of this ain't pretty and basically just exists for a
    # nicer-looking repr. (Iter(), First()) is the equivalent of doing
    # (Iter().filter(spec), Call(first, args=(T,), kwargs={'default':
    # default}))
    __slots__ = ('_spec', '_default', '_first')

    def __init__(self, key=T, default=None):
        self._spec = key
        self._default = default

        spec_glom = Spec(Call(partial, args=(Spec(self._spec).glom,), kwargs={'scope': S}))
        self._first = Call(first, args=(T,), kwargs={'default': default, 'key': spec_glom})

    def glomit(self, target, scope):
        return self._first.glomit(target, scope)

    def __repr__(self):
        cn = self.__class__.__name__
        if self._default is None:
            return f'{cn}({bbrepr(self._spec)})'
        return f'{cn}({bbrepr(self._spec)}, default={bbrepr(self._default)})'


# run_once() status sentinels returned by :class:`FanoutRun`
ROUTED = make_sentinel('ROUTED')              # one source record was processed
BACKPRESSURE = make_sentinel('BACKPRESSURE')  # a BLOCK outlet was full; nothing consumed
EXHAUSTED = make_sentinel('EXHAUSTED')        # the source had no more records
ALL_CLOSED = make_sentinel('ALL_CLOSED')      # every outlet is closed; source untouched

Route = namedtuple('Route', ['name', 'matched', 'reason'])
RoutedRecord = namedtuple('RoutedRecord', ['record', 'result', 'routes'])


def _reason_from_error(error):
    """Turn a glom error raised by an outlet spec into a compact rejection reason."""
    get_message = getattr(error, 'get_message', None)
    if get_message is not None:
        try:
            return get_message()
        except Exception:
            pass
    msgs = getattr(error, 'msgs', None)
    if msgs:
        return '; '.join(msgs)
    return str(error) or error.__class__.__name__


class Outlet:
    """A declarative routing rule for :class:`Fanout`.

    Each outlet pairs a *name* with a *spec* (a glom spec, callable, or
    :class:`~glom.Check`). While fanning out a stream, the spec is applied
    to each record's shared result. If it returns a truthy value without
    raising, the record is *routed* to this outlet; otherwise it is not.

    Args:

       name: A label identifying this outlet (also a key on the run).
       spec: The match spec. A :class:`~glom.Check` (or other spec) that
         raises :class:`~glom.GlomError` on failure is treated as a
         rejection, and the failure message becomes the reason.
       invert: When ``False`` (the default), a record is routed when *spec*
         passes. When ``True``, routing happens when *spec* fails, which lets
         a qualified-items outlet and a rejection-reason outlet share one
         :class:`~glom.Check`.
       include_reason: When ``True``, routed messages are delivered as
         ``(result, reason)`` tuples; otherwise just the result is
         delivered. Defaults to ``False``.
       max_size: Maximum number of messages buffered for this outlet before
         the *on_full* backpressure policy is applied. Defaults to 1000.
       on_full: The backlog policy. One of :data:`BLOCK` (the default; stop
         pulling from the source until the outlet is drained),
         :data:`DROP_NEWEST` (discard the new message and keep flowing), or
         a callable invoked as ``on_full(outlet, message)`` for full control.

    >>> from glom import Check
    >>> qualification = Check(T['score'], validate=lambda s: s >= 60)
    >>> accepted = Outlet('accepted', qualification)
    >>> rejected = Outlet('rejected', qualification, invert=True, include_reason=True)
    >>> rejected
    Outlet('rejected', Check(T['score'], validate=<function ...>), invert=True, include_reason=True)
    """
    def __init__(self, name, spec=T, *, invert=False, include_reason=False,
                 max_size=1000, on_full=BLOCK):
        if not isinstance(max_size, int) or max_size < 1:
            raise TypeError('expected max_size to be a positive int, not: %r' % (max_size,))
        if on_full not in (BLOCK, DROP_NEWEST) and not callable(on_full):
            raise TypeError('expected on_full to be BLOCK, DROP_NEWEST, or a callable,'
                            ' not: %r' % (on_full,))
        self.name = name
        self.spec = spec
        self.invert = invert
        self.include_reason = include_reason
        self.max_size = max_size
        self.on_full = on_full
        self.closed = False
        self.delivered = 0
        self.dropped = 0

    def close(self):
        """Close this outlet. Once every outlet of a :class:`Fanout` is
        closed, its run stops consuming the source."""
        self.closed = True

    def open(self):
        """Reopen a previously :meth:`close`d outlet."""
        self.closed = False

    def __repr__(self):
        kwargs = []
        if self.invert:
            kwargs.append(('invert', self.invert))
        if self.include_reason:
            kwargs.append(('include_reason', self.include_reason))
        if self.max_size != 1000:
            kwargs.append(('max_size', self.max_size))
        if self.on_full is not BLOCK:
            kwargs.append(('on_full', self.on_full))
        return format_invocation(self.__class__.__name__,
                                 (self.name, self.spec), kwargs, repr=bbrepr)


class Fanout:
    """Declaratively fan one input stream out to several local :class:`Outlet`
    rules.

    Every source record has the optional *subspec* applied exactly once; the
    shared result is then offered to each outlet, so a record matching more
    than one outlet is delivered to all of them, each with its own match
    result and rejection reason.

    :meth:`glomit` does not read the input eagerly. It returns a
    :class:`FanoutRun` which pulls one source record at a time, honors each
    outlet's bounded backpressure policy, and stops consuming the source as
    soon as all outlets are closed.

    >>> from glom import Check
    >>> qualification = Check(T['score'], validate=lambda s: s >= 60)
    >>> cleaning_records = [{'id': 1, 'score': 90}, {'id': 2, 'score': 5}]
    >>> fanout = Fanout([
    ...     Outlet('accepted', qualification),
    ...     Outlet('rejected', qualification, invert=True, include_reason=True),
    ... ])
    >>> run = glom(cleaning_records, fanout)
    >>> _ = run.run()
    >>> run.drain('accepted')
    [{'id': 1, 'score': 90}]
    >>> run.drain('rejected')  # reason included for the rejected record
    [({'id': 2, 'score': 5}, ...)]
    """
    def __init__(self, outlets, *, subspec=T):
        if not outlets:
            raise ValueError('expected at least one outlet in %s, got: %r'
                             % (self.__class__.__name__, outlets))
        names = [o.name for o in outlets]
        if len(set(names)) != len(names):
            raise ValueError('outlet names must be unique, got duplicates in: %r' % names)
        self.outlets = list(outlets)
        self.subspec = subspec

    def glomit(self, target, scope):
        return FanoutRun(self, target, scope)

    def __repr__(self):
        if self.subspec is T:
            return format_invocation(self.__class__.__name__,
                                     (self.outlets,), repr=bbrepr)
        return format_invocation(self.__class__.__name__, (self.outlets,),
                                 [('subspec', self.subspec)], repr=bbrepr)


class FanoutRun:
    """The lazy, resumable result of applying a :class:`Fanout` to a target.

    The source is advanced at most one record per :meth:`run_once` call, and
    never while a :data:`BLOCK` outlet is full or after all outlets are
    closed. Each outlet's messages accumulate in a bounded :class:`deque`,
    read with :meth:`drain` (or directly via :meth:`messages`).

    Iterating the run yields a :class:`RoutedRecord` per consumed source
    record, and raises :class:`BackpressureFull` if a :data:`BLOCK` outlet
    fills up without being drained.
    """
    def __init__(self, fanout, target, scope):
        self.fanout = fanout
        self.outlets = fanout.outlets
        self._source = iter(scope[TargetRegistry].get_handler(
            'iterate', target, path=scope[Path])(target))
        self._scope = scope
        self._buffers = {outlet: deque() for outlet in self.outlets}
        self.last_record = None
        self.blocked_outlet = None
        self.records_processed = 0

    def messages(self, outlet):
        """Return the live :class:`deque` of messages buffered for *outlet*
        (name or :class:`Outlet`)."""
        return self._buffers[self._resolve(outlet)]

    def drain(self, outlet):
        """Pop and return every buffered message for *outlet* (name or
        :class:`Outlet`), clearing its buffer to make room for more."""
        buffer = self._buffers[self._resolve(outlet)]
        out = list(buffer)
        buffer.clear()
        return out

    def _resolve(self, outlet):
        if isinstance(outlet, Outlet):
            return outlet
        for candidate in self.outlets:
            if candidate.name == outlet:
                return candidate
        raise KeyError('unknown outlet name: %r' % (outlet,))

    def _active_outlets(self):
        return [outlet for outlet in self.outlets if not outlet.closed]

    def run_once(self):
        """Process at most one source record.

        Returns one of :data:`ROUTED`, :data:`BACKPRESSURE`,
        :data:`EXHAUSTED`, or :data:`ALL_CLOSED`. On :data:`ROUTED`, the
        :class:`RoutedRecord` is available on ``self.last_record``.
        :data:`BACKPRESSURE` and :data:`ALL_CLOSED` leave the source
        iterator untouched.
        """
        active = self._active_outlets()
        if not active:
            self.blocked_outlet = None
            return ALL_CLOSED

        for outlet in active:
            if outlet.on_full is BLOCK and len(self._buffers[outlet]) >= outlet.max_size:
                self.blocked_outlet = outlet
                return BACKPRESSURE
        self.blocked_outlet = None

        try:
            record = next(self._source)
        except StopIteration:
            return EXHAUSTED

        scope = self._scope
        result = record if self.fanout.subspec is T else \
            scope[glom](record, self.fanout.subspec, scope)

        # each distinct rule spec is evaluated at most once for this record,
        # even when several outlets (e.g. accept + inverted reject) share it
        spec_cache = {}
        routes = []
        for outlet in active:
            passed, reason = self._evaluate(outlet.spec, result, scope, spec_cache)
            matched = (not passed) if outlet.invert else passed
            routes.append(Route(outlet.name, matched, reason))
            if not matched:
                continue
            message = (result, reason) if outlet.include_reason else result
            self._deliver(outlet, message)

        routed = RoutedRecord(record, result, routes)
        self.last_record = routed
        self.records_processed += 1
        return ROUTED

    def _evaluate(self, spec, result, scope, spec_cache):
        cache_key = id(spec)
        if cache_key in spec_cache:
            return spec_cache[cache_key]
        try:
            value = scope[glom](result, spec, scope)
        except GlomError as error:
            outcome = (False, _reason_from_error(error))
        except Exception as error:  # plain callables may raise non-glom errors
            outcome = (False, str(error) or error.__class__.__name__)
        else:
            if value is SKIP:
                # Check(..., default=SKIP) and friends explicitly opt out of routing
                outcome = (False, None)
            elif value:
                # a truthy return routes; Check passes the (truthy) target through
                outcome = (True, None)
            else:
                # falsy predicate return is a non-match, mirroring Iter().filter()
                outcome = (False, 'rule %s returned falsy value %r' % (bbrepr(spec), value))
        spec_cache[cache_key] = outcome
        return outcome

    def _deliver(self, outlet, message):
        buffer = self._buffers[outlet]
        if len(buffer) >= outlet.max_size:
            if outlet.on_full is DROP_NEWEST:
                outlet.dropped += 1
                return
            if callable(outlet.on_full):
                outlet.on_full(outlet, message)
                return
            # BLOCK is pre-checked in run_once; keep this as a safe fallback
            self.blocked_outlet = outlet
            raise BackpressureFull(outlet, len(buffer))
        buffer.append(message)
        outlet.delivered += 1

    def run(self):
        """Drive :meth:`run_once` until the source is exhausted or all
        outlets are closed. Raises :class:`BackpressureFull` if a
        :data:`BLOCK` outlet fills without being drained."""
        for _ in self:
            pass
        return self

    def __iter__(self):
        while True:
            status = self.run_once()
            if status is ROUTED:
                yield self.last_record
            elif status is BACKPRESSURE:
                raise BackpressureFull(self.blocked_outlet,
                                       self.blocked_outlet.max_size)
            else:
                return

    def __repr__(self):
        pending = {o.name: len(self._buffers[o]) for o in self.outlets}
        return '%s(%r, processed=%r, pending=%r)' % (
            self.__class__.__name__, self.fanout, self.records_processed, pending)
