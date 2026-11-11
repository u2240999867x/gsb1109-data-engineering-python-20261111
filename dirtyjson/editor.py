"""Position-based, byte-preserving replacement of values in a JSON document.

Parses the document with :class:`dirtyjson.loader.DirtyJSONLoader`, locates
the source span of the value addressed by a path of keys/indices, and
splices the replacement text into exactly that span. Everything outside the
span -- comments, whitespace, quoting style, key order -- is preserved
byte-for-byte.
"""
from __future__ import absolute_import
import json

from .attributed_containers import AttributedDict, AttributedList
from .compat import text_type, string_types
from .loader import DirtyJSONLoader

__all__ = ['replace_value', 'AmbiguousPathError']


class AmbiguousPathError(ValueError):
    """Raised when a path matches more than one value in the document and
    no explicit occurrence was selected."""

    def __init__(self, path, segment, count):
        super(AmbiguousPathError, self).__init__(
            'Path %r is ambiguous: %r matches %d values; '
            'pass occurrence= to choose one' % (tuple(path), segment, count))
        self.path = tuple(path)
        self.segment = segment
        self.count = count


def _select_occurrence(occurrence, path, segment, count):
    if occurrence is None:
        return None
    if isinstance(occurrence, int):
        selected = occurrence
    elif segment in occurrence:
        selected = occurrence[segment]
    elif tuple(path) in occurrence:
        selected = occurrence[tuple(path)]
    else:
        return None
    if not 0 <= selected < count:
        raise IndexError('occurrence %d out of range for %r (%d matches)'
                         % (selected, segment, count))
    return selected


def _value_position(node, segment, path, occurrence):
    """Return the Position of the value addressed by *segment* within *node*."""
    if isinstance(node, AttributedDict):
        occurrences = node.attributes_all(segment)
        if not occurrences:
            raise KeyError('Key %r not found while resolving %r'
                           % (segment, tuple(path)))
        if len(occurrences) > 1:
            selected = _select_occurrence(occurrence, path, segment,
                                          len(occurrences))
            if selected is None:
                raise AmbiguousPathError(path, segment, len(occurrences))
            return occurrences[selected].value
        return occurrences[0].value
    if isinstance(node, (AttributedList, list)):
        if not isinstance(segment, int):
            raise TypeError('Array segment %r must be an int while resolving %r'
                            % (segment, tuple(path)))
        try:
            return node.attributes(segment)
        except IndexError:
            raise IndexError('Index %d out of range while resolving %r'
                             % (segment, tuple(path)))
    raise TypeError('Cannot descend into %r while resolving %r'
                    % (node, tuple(path)))


def _locate(content, path, occurrence=None):
    """Return the (start, end) character span of the value at *path*."""
    loader = DirtyJSONLoader(content)
    node = loader.decode()
    if not path:
        return _root_span(content)
    for depth, segment in enumerate(path):
        position = _value_position(node, segment, path[:depth + 1], occurrence)
        if depth == len(path) - 1:
            return position.index, position.end.index
        # Re-parse the chosen sub-value so positions stay absolute even when
        # it is not the value kept in the tree (duplicate keys keep the last).
        node = DirtyJSONLoader(content).decode(start_index=position.index)
    raise ValueError('Empty path')  # unreachable, kept for clarity


def _root_span(content):
    loader = DirtyJSONLoader(content)
    loader._skip_whitespace()
    start = loader.pos
    loader.scan()
    return start, loader.pos


def replace_value(content, path, new_value, occurrence=None, raw=False):
    """Replace the value at *path* in *content* and return the new document.

    *path* is a sequence of string keys (for objects) and integer indices
    (for arrays) leading from the root to the value to replace. An empty
    path replaces the whole document.

    *new_value* is serialized with :func:`json.dumps` unless *raw* is true,
    in which case it must be a string and is inserted verbatim.

    When duplicate keys make *path* match more than one value,
    :class:`AmbiguousPathError` is raised unless *occurrence* selects one:
    either a single 0-based int applied to any ambiguous segment, or a
    mapping of key name (or path-prefix tuple) to a 0-based index.

    Only the addressed value's source span is modified; every other byte of
    the document, including comments and formatting, is preserved.
    """
    if isinstance(path, text_type) or not hasattr(path, '__iter__'):
        raise TypeError('path must be a sequence of keys/indices, got %r'
                        % (path,))
    path = list(path)
    start, end = _locate(content, path, occurrence)
    if raw:
        if not isinstance(new_value, string_types):
            raise TypeError('raw replacement must be a string, got %r'
                            % (new_value,))
        replacement = new_value
    else:
        replacement = json.dumps(new_value, ensure_ascii=False)
    return content[:start] + replacement + content[end:]
