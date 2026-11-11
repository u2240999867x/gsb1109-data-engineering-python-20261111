"""Migration entry point for moving dirtyjson documents to strict JSON.

Strict JSON (RFC 8259) cannot represent ``NaN``, ``Infinity``,
``-Infinity`` or duplicate object keys, so a lossless one-way conversion
is not always possible. This module splits the migration into two steps:

1. :func:`analyze` parses the source text without losing anything
   (duplicate keys are kept as ordered pairs, non-finite floats are kept
   as floats) and returns a :class:`MigrationIssue` for every value that
   strict JSON cannot express.
2. :func:`migrate` produces the strict JSON document, applying the
   caller's explicit decisions for each problem. ``NaN`` is never
   silently coerced to ``null`` and duplicate keys are never collapsed
   to the last value unless the caller asks for it.

The source text is never modified, and the output of a successful
:func:`migrate` call can be read back with the standard library
:mod:`json` module.
"""
from __future__ import absolute_import
import json
import math

from .error import Error
from .loader import DirtyJSONLoader, UNQUOTED_KEYNAME

__all__ = ['analyze', 'migrate', 'MigrationIssue', 'MigrationError']

NONFINITE_KINDS = ('nan', 'infinity', '-infinity')
DUPLICATE_KEY = 'duplicate_key'


class MigrationError(ValueError):
    """Raised when a document cannot be migrated without caller decisions.

    The ``issues`` attribute lists the :class:`MigrationIssue` items
    that still need a decision.
    """

    def __init__(self, message, issues=()):
        ValueError.__init__(self, message)
        self.issues = list(issues)


class MigrationIssue(object):
    """One spot in the source document that strict JSON cannot express.

    ``kind`` is one of ``'nan'``, ``'infinity'``, ``'-infinity'`` or
    ``'duplicate_key'``. ``path`` is a JSONPath-style location such as
    ``$["servers"][0]["weight"]``. For duplicate keys, ``key`` holds the
    repeated key and ``count`` how many times it appeared.
    """

    def __init__(self, kind, path, key=None, value=None, count=None):
        self.kind = kind
        self.path = path
        self.key = key
        self.value = value
        self.count = count

    def __repr__(self):
        return '%s(kind=%r, path=%r, key=%r, count=%r)' % (
            self.__class__.__name__, self.kind, self.path, self.key,
            self.count)

    def __eq__(self, other):
        return (isinstance(other, MigrationIssue) and
                self.kind == other.kind and self.path == other.path and
                self.key == other.key and self.count == other.count)

    def __ne__(self, other):
        return not self == other


class _ObjectPairs(object):
    """Parsed object kept as an ordered list of key/value pairs.

    Unlike AttributedDict this preserves duplicate keys in source order
    so the caller can decide how to reconcile them.
    """
    __slots__ = ('pairs',)

    def __init__(self):
        self.pairs = []


class _PreservingLoader(DirtyJSONLoader):
    """Loader that keeps duplicate object keys instead of collapsing them."""

    def parse_object(self):
        obj = _ObjectPairs()
        nextchar = self._next_character_after_whitespace()
        while True:
            if nextchar == '}':
                break
            if nextchar == '"' or nextchar == "'":
                key = self.parse_string(nextchar)
            else:
                chunk = UNQUOTED_KEYNAME.match(self.content, self.pos - 1)
                if chunk is None:
                    raise Error("Expecting property name",
                                self.content, self.pos)
                self.pos = chunk.end()
                key = chunk.groups()[0]
            if self._next_character_after_whitespace() != ':':
                raise Error("Expecting ':' delimiter", self.content, self.pos)
            self._skip_whitespace()
            value = self.scan()
            obj.pairs.append((key, value))
            nextchar = self._next_character_after_whitespace()
            if nextchar == '}':
                break
            elif nextchar != ',':
                raise Error("Expecting ',' delimiter or '}'",
                            self.content, self.pos - len(nextchar))
            nextchar = self._next_character_after_whitespace()
        return obj


def _nonfinite_kind(value):
    if math.isnan(value):
        return 'nan'
    return 'infinity' if value > 0 else '-infinity'


def _child_path(path, key):
    return '%s[%s]' % (path, json.dumps(key, ensure_ascii=False))


def _collect_issues(node, path, issues):
    if isinstance(node, _ObjectPairs):
        counts = {}
        for key, _value in node.pairs:
            counts[key] = counts.get(key, 0) + 1
        reported = set()
        for key, value in node.pairs:
            child = _child_path(path, key)
            if counts[key] > 1 and key not in reported:
                reported.add(key)
                issues.append(MigrationIssue(DUPLICATE_KEY, child, key=key,
                                             count=counts[key]))
            _collect_issues(value, child, issues)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _collect_issues(value, '%s[%d]' % (path, index), issues)
    elif isinstance(node, float) and (math.isnan(node) or math.isinf(node)):
        issues.append(MigrationIssue(_nonfinite_kind(node), path, value=node))


def _parse(text):
    return _PreservingLoader(text).decode()


def analyze(text):
    """Parse *text* and list what strict JSON cannot express losslessly.

    Returns a list of :class:`MigrationIssue`, in document order. An
    empty list means :func:`migrate` will succeed without any handler.
    The source text is left untouched.
    """
    issues = []
    _collect_issues(_parse(text), '$', issues)
    return issues


def _convert(node, path, on_nonfinite, on_duplicate):
    if isinstance(node, _ObjectPairs):
        order = []
        grouped = {}
        for key, value in node.pairs:
            if key not in grouped:
                grouped[key] = []
                order.append(key)
            grouped[key].append(value)
        result = {}
        for key in order:
            child = _child_path(path, key)
            values = [_convert(value, child, on_nonfinite, on_duplicate)
                      for value in grouped[key]]
            if len(values) == 1:
                result[key] = values[0]
            elif on_duplicate == 'first':
                result[key] = values[0]
            elif on_duplicate == 'last':
                result[key] = values[-1]
            else:
                result[key] = on_duplicate(key, values)
        return result
    if isinstance(node, list):
        return [_convert(value, '%s[%d]' % (path, index),
                         on_nonfinite, on_duplicate)
                for index, value in enumerate(node)]
    if isinstance(node, float) and (math.isnan(node) or math.isinf(node)):
        issue = MigrationIssue(_nonfinite_kind(node), path, value=node)
        replacement = (on_nonfinite(issue) if callable(on_nonfinite)
                       else on_nonfinite)
        if (isinstance(replacement, float) and
                (math.isnan(replacement) or math.isinf(replacement))):
            raise MigrationError(
                'on_nonfinite returned a non-finite float at %s; strict '
                'JSON cannot express it' % path, [issue])
        return replacement
    return node


def migrate(text, on_nonfinite=None, on_duplicate=None):
    """Convert *text* to a strict JSON document string.

    *on_nonfinite* decides how ``NaN``/``Infinity``/``-Infinity`` are
    written: either a callable receiving the :class:`MigrationIssue` and
    returning a replacement value, or a plain replacement value used for
    every occurrence.

    *on_duplicate* decides how repeated object keys are reconciled:
    ``'first'`` keeps the first value, ``'last'`` keeps the last one,
    and a callable ``(key, values)`` receives all values in source
    order and returns the one to keep.

    If the document contains values strict JSON cannot express and the
    corresponding handler was not given, :class:`MigrationError` is
    raised listing the open :class:`MigrationIssue` items; nothing is
    coerced silently. On success the returned string can be parsed by
    the standard library :mod:`json` module. The source text is never
    modified.
    """
    tree = _parse(text)
    issues = []
    _collect_issues(tree, '$', issues)
    unhandled = [
        issue for issue in issues
        if (issue.kind == DUPLICATE_KEY and on_duplicate is None) or
        (issue.kind in NONFINITE_KINDS and on_nonfinite is None)
    ]
    if unhandled:
        raise MigrationError(
            'source contains %d value(s) strict JSON cannot express; '
            'pass on_nonfinite and/or on_duplicate to decide how to '
            'handle them' % len(unhandled), unhandled)
    converted = _convert(tree, '$', on_nonfinite, on_duplicate)
    return json.dumps(converted, allow_nan=False)
