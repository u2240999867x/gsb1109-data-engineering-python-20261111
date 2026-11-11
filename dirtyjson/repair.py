"""Suggest minimal punctuation repairs for broken JSON-ish config files.

This module never modifies the parser and never touches the file on disk.
It searches for the smallest set of insertions/deletions of structural
punctuation (``{ } [ ] , :``) that makes the document parse under the
normal, lenient :func:`dirtyjson.loads` semantics, and returns every
equally-valid candidate together with the exact range of each edit so a
human can review the suggestions.

Keys, values and string contents are never altered: only the six
structural punctuation characters may be inserted or deleted, and a
previously inserted character may not be deleted again.

The acceptance range of the parser itself is unchanged; the only extra
requirement for a repair candidate is that the parse consumes the whole
document (otherwise trailing garbage would make any truncation look
valid).
"""
from __future__ import absolute_import
from collections import defaultdict

from .error import linecol
from .loader import DirtyJSONLoader

STRUCTURAL_PUNCTUATION = '{}[],:'
# Insertion order matters only for search priority: commas and colons are
# the most commonly missing punctuation in hand-written configs.
INSERTABLE_PUNCTUATION = ',:{[}]'
_DELETABLE_PUNCTUATION = frozenset(STRUCTURAL_PUNCTUATION)

__all__ = ['Edit', 'RepairCandidate', 'RepairResult', 'suggest_repairs',
           'apply_edits', 'STRUCTURAL_PUNCTUATION']


class Edit(object):
    """One insertion or deletion of a single structural punctuation char.

    ``pos``/``end`` are offsets into the *original* document.  An insert
    has ``pos == end`` and places ``char`` before that offset; a delete
    removes the original character spanning ``[pos, end)``.
    """
    __slots__ = ('action', 'char', 'pos', 'end')

    def __init__(self, action, char, pos):
        if action not in ('insert', 'delete'):
            raise ValueError('action must be insert or delete')
        self.action = action
        self.char = char
        self.pos = pos
        self.end = pos if action == 'insert' else pos + 1

    def __repr__(self):
        return 'Edit(%r, %r, %r)' % (self.action, self.char, self.pos)

    def __eq__(self, other):
        return (isinstance(other, Edit) and self.action == other.action and
                self.char == other.char and self.pos == other.pos)

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash((self.action, self.char, self.pos))

    def describe(self, content):
        """Human-readable location of this edit in the original document."""
        line, col = linecol(content, self.pos)
        if self.action == 'insert':
            return "insert %r before line %d column %d (char %d)" % (
                self.char, line, col, self.pos)
        return "delete %r at line %d column %d (char %d)" % (
            self.char, line, col, self.pos)


class RepairCandidate(object):
    """One minimal repair: the edits (original coordinates), the repaired
    text and the value it parses to."""
    __slots__ = ('edits', 'text', 'value')

    def __init__(self, edits, text, value):
        self.edits = tuple(edits)
        self.text = text
        self.value = value

    @property
    def distance(self):
        return len(self.edits)

    def __repr__(self):
        return 'RepairCandidate(distance=%d, edits=%r)' % (
            self.distance, list(self.edits))


class RepairResult(object):
    """Outcome of :func:`suggest_repairs`.

    ``candidates`` holds every minimal-edit repair found.  ``complete`` is
    False when the search budget ran out, meaning more equally-good or
    better candidates may exist; nothing is auto-selected either way.
    ``already_valid`` is True when the original document needed no repair.
    """
    __slots__ = ('candidates', 'complete', 'attempts', 'already_valid')

    def __init__(self, candidates, complete, attempts, already_valid=False):
        self.candidates = list(candidates)
        self.complete = complete
        self.attempts = attempts
        self.already_valid = already_valid

    def __repr__(self):
        return ('RepairResult(candidates=%d, complete=%r, attempts=%d, '
                'already_valid=%r)' % (len(self.candidates), self.complete,
                                       self.attempts, self.already_valid))


def apply_edits(content, edits):
    """Apply ``edits`` (original-document coordinates) to ``content``."""
    inserts = defaultdict(list)
    deletes = set()
    for edit in edits:
        if edit.action == 'insert':
            inserts[edit.pos].append(edit.char)
        else:
            deletes.add(edit.pos)
    out = []
    size = len(content)
    for i in range(size + 1):
        if i in inserts:
            out.extend(inserts[i])
        if i < size and i not in deletes:
            out.append(content[i])
    return ''.join(out)


def _position_mapping(content, edits):
    """Map each index of the edited text to its original-document index,
    or None for inserted characters."""
    inserts = defaultdict(int)
    deletes = set()
    for edit in edits:
        if edit.action == 'insert':
            inserts[edit.pos] += 1
        else:
            deletes.add(edit.pos)
    mapping = []
    size = len(content)
    for i in range(size + 1):
        mapping.extend([None] * inserts.get(i, 0))
        if i < size and i not in deletes:
            mapping.append(i)
    return mapping


def _original_anchor(mapping, pos, size):
    """Original offset before which an insertion at edited-text position
    ``pos`` should be recorded."""
    for j in range(pos, len(mapping)):
        if mapping[j] is not None:
            return mapping[j]
    return size


def _parse_outcome(text, parse_kwargs):
    """Parse ``text`` with the stock lenient loader.

    Returns ``(ok, value, error_position)``.  ``ok`` is True only when the
    document parses and nothing but whitespace/comments remains.
    """
    loader = DirtyJSONLoader(text, **parse_kwargs)
    try:
        value = loader.decode()
        loader._skip_whitespace()
    except Exception as exc:
        pos = getattr(exc, 'pos', None)
        if pos is None:
            pos = min(loader.pos, len(text))
        return False, None, pos
    if loader.pos < loader.end:
        return False, None, loader.pos
    return True, value, None


def _candidate_edits(content, text, edits, pos, window):
    """Yield follow-up edit tuples targeting the failure at ``pos``."""
    mapping = _position_mapping(content, edits)
    size = len(content)
    lo = max(0, pos - window)
    hi = min(len(text), pos + window)
    for q in range(lo, hi + 1):
        anchor = _original_anchor(mapping, q, size)
        for char in INSERTABLE_PUNCTUATION:
            yield edits + (Edit('insert', char, anchor),)
    last = min(hi, len(text) - 1)
    for q in range(lo, last + 1):
        if text[q] in _DELETABLE_PUNCTUATION and mapping[q] is not None:
            yield edits + (Edit('delete', text[q], mapping[q]),)


def suggest_repairs(content, max_edits=4, budget=10000, window=1,
                    parse_kwargs=None):
    """Search for minimal punctuation repairs of ``content``.

    Edits are generated breadth-first around each parse failure, so every
    candidate returned has the same, smallest edit distance and all
    equally-valid interpretations are kept.  ``budget`` caps the number of
    trial parses; when it runs out the result is returned with
    ``complete=False`` instead of silently picking one candidate.
    ``window`` controls how far (in characters) around each failure
    position edits are proposed.
    """
    parse_kwargs = parse_kwargs or {}
    attempts = 0
    visited = {content}
    frontier = [(content, ())]
    candidates = []
    already_valid = False
    complete = True
    depth = 0
    while frontier and not candidates and not already_valid:
        next_frontier = []
        for text, edits in frontier:
            if attempts >= budget:
                complete = False
                return RepairResult(candidates, complete, attempts,
                                    already_valid)
            ok, value, pos = _parse_outcome(text, parse_kwargs)
            attempts += 1
            if ok:
                if edits:
                    candidates.append(RepairCandidate(edits, text, value))
                else:
                    already_valid = True
                continue
            if depth < max_edits and not candidates:
                for new_edits in _candidate_edits(content, text, edits, pos,
                                                  window):
                    new_text = apply_edits(content, new_edits)
                    if new_text not in visited:
                        visited.add(new_text)
                        next_frontier.append((new_text, new_edits))
        frontier = next_frontier
        depth += 1
    return RepairResult(candidates, complete, attempts, already_valid)


def main(argv=None):
    import argparse
    import io
    import sys

    parser = argparse.ArgumentParser(
        description='Suggest minimal punctuation repairs for a broken '
                    'JSON config. The file itself is never modified.')
    parser.add_argument('path', help='config file to inspect')
    parser.add_argument('--max-edits', type=int, default=4,
                        help='maximum edits per candidate (default 4)')
    parser.add_argument('--budget', type=int, default=10000,
                        help='maximum trial parses (default 10000)')
    parser.add_argument('--window', type=int, default=1,
                        help='characters around each failure to consider '
                             '(default 1)')
    args = parser.parse_args(argv)

    with io.open(args.path, encoding='utf-8') as fp:
        content = fp.read()
    result = suggest_repairs(content, max_edits=args.max_edits,
                             budget=args.budget, window=args.window)

    if result.already_valid:
        print('%s already parses cleanly; no repair needed.' % args.path)
        return 0
    if not result.complete:
        print('warning: search budget exhausted after %d trial parses; '
              'suggestions may be incomplete.' % result.attempts,
              file=sys.stderr)
    if not result.candidates:
        print('no repair found within %d edit(s); file left untouched.'
              % args.max_edits)
        return 1
    for number, candidate in enumerate(result.candidates, 1):
        print('candidate %d (%d edit%s):' % (
            number, candidate.distance, '' if candidate.distance == 1 else 's'))
        for edit in candidate.edits:
            print('  - %s' % edit.describe(content))
        print('  repaired text:')
        for line in candidate.text.splitlines():
            print('    %s' % line)
    print('review the candidates above; the original file was not modified.')
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
