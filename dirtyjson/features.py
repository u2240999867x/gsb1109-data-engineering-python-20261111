"""Named lenient (non-standard JSON) syntax features understood by dirtyjson.

Each feature has a stable string name so callers can ask which lenient
constructs a document relied on (see ``loads_with_features``) and can
restrict which of them are accepted (see the ``allowed_features`` argument
of ``load``/``loads``).
"""
from __future__ import absolute_import

__all__ = ['FeatureUse', 'ALL_FEATURES',
           'SINGLE_QUOTED_STRINGS', 'COMMENTS', 'TRAILING_COMMAS',
           'UNQUOTED_KEYS', 'HEXADECIMAL_NUMBERS', 'OCTAL_NUMBERS',
           'EXPRESSIONS', 'UNESCAPED_CONTROL_CHARACTERS']

#: Strings delimited by single quotes, e.g. ``'value'``. A single quote
#: appearing inside a double-quoted string is just content and does not
#: count as this feature.
SINGLE_QUOTED_STRINGS = 'single_quoted_strings'

#: ``//`` line comments and ``/* ... */`` block comments.
COMMENTS = 'comments'

#: A comma directly before the closing ``}`` or ``]`` of an object or array.
TRAILING_COMMAS = 'trailing_commas'

#: Object keys written as bare words, e.g. ``{key: 1}``.
UNQUOTED_KEYS = 'unquoted_keys'

#: Integers written in hexadecimal, e.g. ``0x2A``.
HEXADECIMAL_NUMBERS = 'hexadecimal_numbers'

#: Integers written with a leading zero (octal), e.g. ``077``.
OCTAL_NUMBERS = 'octal_numbers'

#: Arithmetic expressions used as values, e.g. ``[1+2]``.
EXPRESSIONS = 'expressions'

#: Literal (unescaped) control characters inside strings.
UNESCAPED_CONTROL_CHARACTERS = 'unescaped_control_characters'

#: Every lenient feature dirtyjson knows how to parse. This is the default
#: ``allowed_features`` set, so ``loads`` stays as permissive as ever.
ALL_FEATURES = frozenset([
    SINGLE_QUOTED_STRINGS,
    COMMENTS,
    TRAILING_COMMAS,
    UNQUOTED_KEYS,
    HEXADECIMAL_NUMBERS,
    OCTAL_NUMBERS,
    EXPRESSIONS,
    UNESCAPED_CONTROL_CHARACTERS,
])


class FeatureUse(object):
    """Record of one use of a lenient syntax feature in a parsed document.

    feature: the name of the feature (one of the constants in this module)
    pos: the 0-based character offset in the document where it occurs
    line: the 1-based line number corresponding to pos
    column: the 1-based column number corresponding to pos
    """
    __slots__ = ('feature', 'pos', 'line', 'column')

    def __init__(self, feature, pos, line, column):
        self.feature = feature
        self.pos = pos
        self.line = line
        self.column = column

    def __repr__(self):
        return '%s(%r, pos=%r, line=%r, column=%r)' % (
            self.__class__.__name__, self.feature, self.pos,
            self.line, self.column)

    def __eq__(self, other):
        if not isinstance(other, FeatureUse):
            return NotImplemented
        return (self.feature == other.feature and self.pos == other.pos and
                self.line == other.line and self.column == other.column)

    def __ne__(self, other):
        result = self.__eq__(other)
        if result is NotImplemented:
            return result
        return not result

    def __hash__(self):
        return hash((self.feature, self.pos, self.line, self.column))
