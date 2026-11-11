"""Locate JSON value candidates embedded in dirty text.

An interface response is not always clean JSON: diagnostic lines, log
output, or script fragments may surround the actual payload.  This
module scans such text lexically, skipping string literals and comments
in the surrounding noise (so a ``{`` inside a quoted diagnostic string
is never mistaken for a candidate), and tries to decode a value at
every ``{`` or ``[`` that appears outside of them.

Every span that decodes is reported as a :class:`Candidate` with exact
``start``/``end`` offsets so a human can choose which one to promote to
production.  Nothing is picked automatically: if two spans both parse,
both are returned in document order.  Structures that run into the end
of the text before they close are reported too, with
``boundary_confident`` set to ``False``, because their lexical end
cannot be confirmed.
"""
from __future__ import absolute_import

from .compat import PY2, binary_type
from .error import Error
from .loader import DirtyJSONLoader

__all__ = ['Candidate', 'find_candidates']

_STRING_QUOTES = '"\'`'
_UNTERMINATED = 'Unterminated'


class Candidate(object):
    """A span of text that may be a JSON value.

    ``start`` and ``end`` are character offsets into the scanned text
    (``end`` is exclusive), so ``text[candidate.start:candidate.end]``
    is exactly the span that was decoded.  ``value`` holds the decoded
    Python object, or ``None`` when the span could not be fully
    decoded.  ``boundary_confident`` is ``False`` when the lexical end
    of the value could not be confirmed (the text ends before the
    structure closes); in that case ``end`` is just the end of the
    available text and ``error`` holds the decoding error.
    """

    def __init__(self, start, end, value, boundary_confident, error=None):
        self.start = start
        self.end = end
        self.value = value
        self.boundary_confident = boundary_confident
        self.error = error

    def __repr__(self):
        return '%s(start=%r, end=%r, value=%r, boundary_confident=%r)' % (
            type(self).__name__, self.start, self.end, self.value,
            self.boundary_confident)


def _skip_string(content, pos):
    """Return the index just past the string literal opening at *pos*."""
    quote = content[pos]
    pos += 1
    end = len(content)
    while pos < end:
        char = content[pos]
        if char == '\\':
            pos += 2
            continue
        pos += 1
        if char == quote:
            break
    return pos


def _value_starts(content):
    """Yield indexes of ``{`` or ``[`` occurring outside string literals
    and comments."""
    pos = 0
    end = len(content)
    while pos < end:
        char = content[pos]
        if char in _STRING_QUOTES:
            pos = _skip_string(content, pos)
        elif char == '/' and content[pos + 1:pos + 2] == '/':
            newline = content.find('\n', pos + 2)
            pos = end if newline < 0 else newline + 1
        elif char == '/' and content[pos + 1:pos + 2] == '*':
            close = content.find('*/', pos + 2)
            pos = end if close < 0 else close + 2
        else:
            if char == '{' or char == '[':
                yield pos
            pos += 1


def _is_truncation(error, content):
    """True when *error* indicates the text ended before the value closed."""
    return error.pos >= len(content) or error.msg.startswith(_UNTERMINATED)


def find_candidates(content, encoding=None, parse_float=None, parse_int=None,
                    parse_constant=None):
    """Scan *content* for JSON value candidates and return them all as a
    list of :class:`Candidate`, in document order.

    Decoding uses the same lenient rules as :func:`dirtyjson.loads`
    (unquoted keys, single quotes, comments, ``NaN``/``Infinity``, hex
    numbers, dangling commas), and *parse_float*, *parse_int* and
    *parse_constant* behave as they do there.

    Candidates are reported, never chosen: when several spans parse,
    every one of them is returned so a human can decide which belongs
    in production.  Spans whose structure is cut off by the end of the
    text are included with ``boundary_confident`` set to ``False``.
    """
    if not PY2 and isinstance(content, binary_type):
        content = content.decode()
    candidates = []
    for start in _value_starts(content):
        if candidates and start < candidates[-1].end:
            # Inside a candidate that was already reported.
            continue
        loader = DirtyJSONLoader(content, encoding, parse_float, parse_int,
                                 parse_constant)
        try:
            value = loader.decode(start_index=start)
        except Error as error:
            if _is_truncation(error, content):
                candidates.append(
                    Candidate(start, len(content), None, False, error))
            continue
        candidates.append(Candidate(start, loader.pos, value, True))
    return candidates
