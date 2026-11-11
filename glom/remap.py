"""Normalize mapping keys before the data lands in your data store.

Partner feeds often spell the same product key different ways
(``"A-01"`` vs. ``"a01"``). :class:`Remap` rewrites every key of a
mapping through a *key* spec, and optionally each business value
through a *value* spec, while keeping the original keys around for
error reporting.

When two or more original keys normalize to the same key, the caller
decides what happens via the ``on_collision`` argument:

* :data:`REJECT` (the default) raises a :class:`KeyCollisionError`,
* :data:`COLLECT` groups the colliding values into a list,
* :data:`MERGE` combines them with a caller-supplied (or default
  mapping) merger.

Key-transform failures and value-transform failures are reported as
distinct exception types, and every error retains the original key it
stemmed from. All errors encountered in one pass are reported together,
so a single bad entry never causes the rest of the mapping's problems
to be hidden.
"""
from collections import OrderedDict, namedtuple
from pprint import pprint

from boltons.typeutils import make_sentinel

from .core import T, Path, SKIP, glom, GlomError, format_invocation, bbrepr

_MISSING = make_sentinel('_MISSING')

#: Raise :class:`KeyCollisionError` when normalized keys collide.
REJECT = make_sentinel('REJECT')
REJECT.__doc__ = 'Raise :class:`KeyCollisionError` when normalized keys collide.'
#: Collect all values sharing a normalized key into a list.
COLLECT = make_sentinel('COLLECT')
COLLECT.__doc__ = 'Collect all values sharing a normalized key into a list.'
#: Merge all values sharing a normalized key with the merger.
MERGE = make_sentinel('MERGE')
MERGE.__doc__ = 'Merge all values sharing a normalized key with the merger.'

#: One original mapping entry as seen by :class:`Remap`.
#:
#: * ``orig_key``: the key as it appeared in the input mapping
#: * ``norm_key``: the key after the key transform
#: * ``value``: the transformed value, or ``None`` when ``error`` is set
#: * ``error``: the exception raised by the value transform, if any
RemapEntry = namedtuple('RemapEntry', ['orig_key', 'norm_key', 'value', 'error'])


class RemapError(GlomError):
    """Base class for errors raised while processing a :class:`Remap`.

    A single failed entry raises the matching subtype
    (:class:`KeyTransformError`, :class:`ValueTransformError`, or
    :class:`KeyCollisionError`) directly. When one pass encounters
    multiple failures, a :class:`RemapError` is raised instead, holding
    every individual failure in its ``errors`` attribute. The
    ``key_errors``, ``value_errors``, and ``collision_errors``
    properties split those failures apart by category, and work on
    single-failure exceptions as well.
    """
    def __init__(self, errors=None, path=(), orig_key=_MISSING, norm_key=_MISSING):
        # NB: like the other GlomError subtypes, don't call super().__init__();
        # leaving the positional args in self.args keeps copy.copy() working
        # (the top-level glom() copies errors before finalizing them)
        self.errors = tuple(errors) if errors is not None else (self,)
        self.path = path
        self.orig_key = orig_key
        self.norm_key = norm_key

    @property
    def key_errors(self):
        return [e for e in self.errors if isinstance(e, KeyTransformError)]

    @property
    def value_errors(self):
        return [e for e in self.errors if isinstance(e, ValueTransformError)]

    @property
    def collision_errors(self):
        return [e for e in self.errors if isinstance(e, KeyCollisionError)]

    def _path_suffix(self):
        return ' (at path %r)' % (self.path,) if self.path else ''

    def get_message(self):
        # a directly-raised, single failure keeps itself in .errors; the
        # concrete subtypes below override get_message() for that case
        if not self.errors or self.errors[0] is self:
            return '%s (at path %r)' % (type(self).__name__, self.path)
        if len(self.errors) == 1:
            return self.errors[0].get_message()
        lines = ['remap failed with %d error(s):' % len(self.errors)]
        lines.extend(' - %s' % e.get_message() for e in self.errors)
        return '\n'.join(lines)

    def __repr__(self):
        cn = self.__class__.__name__
        if not self.errors or self.errors[0] is self:
            return '%s(orig_key=%r, norm_key=%r, path=%r)' % (
                cn, self.orig_key, self.norm_key, self.path)
        return '%s(%r, %r)' % (cn, list(self.errors), self.path)


class KeyTransformError(RemapError):
    """Raised when the key transform fails for an original key.

    The offending ``orig_key`` is always preserved, along with the
    underlying exception in ``exc``. When the transform returned a
    value that could not be used as a mapping key (e.g. it was
    unhashable), ``norm_key`` holds that value.
    """
    def __init__(self, orig_key, exc, path=(), norm_key=_MISSING):
        self.exc = exc
        super().__init__(path=path, orig_key=orig_key, norm_key=norm_key)

    def get_message(self):
        if self.norm_key is _MISSING:
            msg = ('key transform failed for original key %r, got error: %r'
                   % (self.orig_key, self.exc))
        else:
            msg = ('key transform produced unusable key %r for original key'
                   ' %r, got error: %r'
                   % (self.norm_key, self.orig_key, self.exc))
        return msg + self._path_suffix()


class ValueTransformError(RemapError):
    """Raised when the value transform fails for an entry.

    Carries both the ``orig_key`` and the normalized ``norm_key`` the
    value was destined for, plus the underlying exception in ``exc``.
    """
    def __init__(self, orig_key, norm_key, exc, path=()):
        self.exc = exc
        super().__init__(path=path, orig_key=orig_key, norm_key=norm_key)

    def get_message(self):
        return ('value transform failed for original key %r (normalized key'
                ' %r), got error: %r%s'
                % (self.orig_key, self.norm_key, self.exc, self._path_suffix()))


class KeyCollisionError(RemapError):
    """Raised under :data:`REJECT` when original keys normalize to the
    same key.

    ``key`` is the normalized key, ``orig_keys`` lists every original
    key that mapped to it (in input order), and ``entries`` holds the
    full :class:`RemapEntry` records, including any whose value
    transform failed.
    """
    def __init__(self, norm_key, entries, path=()):
        self.key = norm_key
        self.entries = tuple(entries)
        self.orig_keys = tuple(entry.orig_key for entry in entries)
        super().__init__(path=path, orig_key=self.orig_keys[0], norm_key=norm_key)

    def get_message(self):
        return ('collision on normalized key %r from original keys %r;'
                ' set on_collision to COLLECT or MERGE to handle this'
                ' explicitly%s'
                % (self.key, self.orig_keys, self._path_suffix()))


def _default_merge(values):
    merged = {}
    for value in values:
        try:
            merged.update(value)
        except (TypeError, ValueError) as exc:
            raise TypeError('default Remap merger expects mapping values,'
                            ' got %r (error: %r)' % (value, exc))
    return merged


class Remap:
    """Transform the keys and values of a mapping, with explicit
    collision handling.

    Args:
       key: A glomspec applied to each original key. Defaults to ``T``,
          passing keys through unchanged.
       value: A glomspec applied to each value. Defaults to ``T``,
          passing values through unchanged.
       on_collision: One of :data:`REJECT` (default), :data:`COLLECT`,
          or :data:`MERGE`.
       merge: A callable taking the list of colliding values and
          returning the merged value. Required when
          ``on_collision=MERGE``; defaults to a mapping merger.
       spec: An optional sub-spec locating the mapping to remap within
          the target.

    Every entry's value transform is evaluated, even when its
    normalized key collides with another entry, so no input value is
    silently dropped in favor of a "last key wins" result.

    Basic normalization, rejecting collisions::

      >>> norm = lambda key: key.replace('-', '').lower()
      >>> target = {'A-01': {'qty': 1}, 'a01': {'qty': 2}}
      >>> glom(target, Remap(key=norm))
      Traceback (most recent call last):
      ...
      KeyCollisionError: collision on normalized key 'a01' from original keys ('A-01', 'a01')...

    Collecting the colliding values instead::

      >>> pprint(glom(target, Remap(key=norm, on_collision=COLLECT)))
      {'a01': [{'qty': 1}, {'qty': 2}]}

    Merging them explicitly::

      >>> spec = Remap(key=norm, on_collision=MERGE,
      ...              merge=lambda vals: {'qty': sum(v['qty'] for v in vals)})
      >>> glom(target, spec)
      {'a01': {'qty': 3}}

    Key-transform and value-transform failures are distinct error
    types, each keeping the original key::

      >>> def bad_key(key):
      ...     raise RuntimeError('key exploded')
      >>> glom({'A-01': 1}, Remap(key=bad_key))
      Traceback (most recent call last):
      ...
      KeyTransformError: key transform failed for original key 'A-01', got error: RuntimeError('key exploded')

      >>> glom({'A-01': 'not-an-int'}, Remap(value=int))
      Traceback (most recent call last):
      ...
      ValueTransformError: value transform failed for original key 'A-01' (normalized key 'A-01'), got error: ValueError(...)
    """
    def __init__(self, key=T, value=T, on_collision=REJECT, merge=None, spec=T):
        if (on_collision is not REJECT and on_collision is not COLLECT
                and on_collision is not MERGE):
            raise ValueError('expected on_collision to be one of REJECT, COLLECT,'
                             ' or MERGE, not: %r' % (on_collision,))
        if merge is None:
            merge = _default_merge if on_collision is MERGE else None
        elif not callable(merge):
            raise TypeError('expected merge to be callable, not: %r' % (merge,))
        elif on_collision is not MERGE:
            raise ValueError('merge is only supported with on_collision=MERGE')

        self.key = key
        self.value = value
        self.on_collision = on_collision
        self.merge = merge
        self.spec = spec

    def glomit(self, target, scope):
        if self.spec is not T:
            target = scope[glom](target, self.spec, scope)
        try:
            items = list(target.items())
        except AttributeError:
            raise TypeError('expected Remap target to be a mapping with .items(),'
                            ' not: %r' % (type(target).__name__,))

        path = scope[Path]
        key_errors = []
        value_errors = []
        groups = OrderedDict()

        for orig_key, orig_value in items:
            try:
                norm_key = scope[glom](orig_key, self.key, scope)
            except Exception as exc:
                key_errors.append(KeyTransformError(orig_key, exc, path))
                continue

            try:
                new_value = scope[glom](orig_value, self.value, scope)
            except Exception as exc:
                value_errors.append(ValueTransformError(orig_key, norm_key, exc, path))
                entry = RemapEntry(orig_key, norm_key, None, exc)
            else:
                if new_value is SKIP:
                    # glom's usual "omit this entry" signal; a skipped entry
                    # takes no part in collision handling
                    continue
                entry = RemapEntry(orig_key, norm_key, new_value, None)

            try:
                groups.setdefault(norm_key, []).append(entry)
            except TypeError as exc:
                # e.g. the normalized key turned out to be unhashable;
                # setdefault() failed before mutating any group, so no
                # cleanup is needed
                key_errors.append(KeyTransformError(orig_key, exc, path, norm_key=norm_key))

        collision_errors = []
        if self.on_collision is REJECT:
            for norm_key, entries in groups.items():
                if len(entries) > 1:
                    collision_errors.append(KeyCollisionError(norm_key, tuple(entries), path))

        errors = key_errors + value_errors + collision_errors
        if errors:
            if len(errors) == 1:
                raise errors[0]
            raise RemapError(errors, path)

        ret = type(target)()
        for norm_key, entries in groups.items():
            values = [entry.value for entry in entries if entry.error is None]
            if len(entries) == 1:
                ret[norm_key] = values[0]
            elif self.on_collision is COLLECT:
                ret[norm_key] = values
            else:  # MERGE; REJECT collisions already raised above
                ret[norm_key] = self.merge(values)
        return ret

    def __repr__(self):
        cn = self.__class__.__name__
        args = (self.key,) if self.key is not T else ()
        kwargs = []
        if self.value is not T:
            kwargs.append(('value', self.value))
        if self.on_collision is not REJECT:
            kwargs.append(('on_collision', self.on_collision))
        if self.on_collision is MERGE and self.merge is not _default_merge:
            kwargs.append(('merge', self.merge))
        if self.spec is not T:
            kwargs.append(('spec', self.spec))

        def _repr(value):
            if value is REJECT or value is COLLECT or value is MERGE:
                return value.name
            return bbrepr(value)

        return format_invocation(cn, args, kwargs, repr=_repr)


def remap(target, key=T, value=T, on_collision=REJECT, merge=None, spec=T):
    """Remap the keys (and optionally values) of *target* in one call.

    A thin convenience wrapper around the :class:`Remap` specifier::

      >>> target = {'A-01': 1, 'B-02': 2}
      >>> remap(target, key=str.lower, on_collision=COLLECT)
      {'a-01': 1, 'b-02': 2}

    See :class:`Remap` for the full semantics, including collision
    handling and the exception types.
    """
    return glom(target, Remap(key=key, value=value, on_collision=on_collision,
                              merge=merge, spec=spec))
