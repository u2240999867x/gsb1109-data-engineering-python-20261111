from __future__ import absolute_import
from .error import Error
from .features import (ALL_FEATURES, FeatureUse, SINGLE_QUOTED_STRINGS,
                       COMMENTS, TRAILING_COMMAS, UNQUOTED_KEYS,
                       HEXADECIMAL_NUMBERS, OCTAL_NUMBERS, EXPRESSIONS,
                       UNESCAPED_CONTROL_CHARACTERS)
from .loader import DirtyJSONLoader

r"""JSON (JavaScript Object Notation) <http://json.org> is a subset of
JavaScript syntax (ECMA-262 3rd edition) used as a lightweight data
interchange format.

:mod:`dirtyjson` exposes an API familiar to users of the standard library
:mod:`marshal` and :mod:`pickle` modules. It is the externally maintained
version of the :mod:`json` library contained in Python 2.6, but maintains
compatibility with Python 2.4 and Python 2.5 and (currently) has
significant performance advantages, even without using the optional C
extension for speedups.

Decoding JSON::

    >>> import dirtyjson
    >>> obj = [u'foo', {u'bar': [u'baz', None, 1.0, 2]}]
    >>> dirtyjson.loads('["foo", {"bar":["baz", null, 1.0, 2]}]') == obj
    True
    >>> dirtyjson.loads('"\\"foo\\bar"') == u'"foo\x08ar'
    True
    >>> from dirtyjson.compat import StringIO
    >>> io = StringIO('["streaming API"]')
    >>> dirtyjson.load(io)[0] == 'streaming API'
    True

Decoding JSON and reporting which lenient syntax features were used::

    >>> obj, features = dirtyjson.loads_with_features("{key: 'value',}")
    >>> obj == {u'key': u'value'}
    True
    >>> [(f.feature, f.line, f.column) for f in features]
    [('unquoted_keys', 1, 2), ('single_quoted_strings', 1, 7), ('trailing_commas', 1, 14)]

"""
__all__ = ['load', 'loads', 'load_with_features', 'loads_with_features',
           'Error', 'FeatureUse', 'ALL_FEATURES',
           'SINGLE_QUOTED_STRINGS', 'COMMENTS', 'TRAILING_COMMAS',
           'UNQUOTED_KEYS', 'HEXADECIMAL_NUMBERS', 'OCTAL_NUMBERS',
           'EXPRESSIONS', 'UNESCAPED_CONTROL_CHARACTERS']

__author__ = 'Scott Maxwell <scott@codecobblers.com>'


def load(fp, encoding=None, parse_float=None, parse_int=None,
         parse_constant=None, search_for_first_object=False,
         allowed_features=None):
    """Deserialize ``fp`` (a ``.read()``-supporting file-like object containing
    a JSON document) to a Python object.

    *encoding* determines the encoding used to interpret any
    :class:`str` objects decoded by this instance (``'utf-8'`` by
    default).  It has no effect when decoding :class:`unicode` objects.

    Note that currently only encodings that are a superset of ASCII work,
    strings of other encodings should be passed in as :class:`unicode`.

    *parse_float*, if specified, will be called with the string of every
    JSON float to be decoded.  By default, this is equivalent to
    ``float(num_str)``. This can be used to use another data type or parser
    for JSON floats (e.g. :class:`decimal.Decimal`).

    *parse_int*, if specified, will be called with the string of every
    JSON int to be decoded.  By default, this is equivalent to
    ``int(num_str)``.  This can be used to use another data type or parser
    for JSON integers (e.g. :class:`float`).

    *parse_constant*, if specified, will be called with one of the
    following strings: ``'-Infinity'``, ``'Infinity'``, ``'NaN'``.  This
    can be used to raise an exception if invalid JSON numbers are
    encountered.

    *allowed_features*, if specified, is an iterable of lenient syntax
    feature names (see :mod:`dirtyjson.features`) that the document is
    permitted to use.  Any other lenient feature raises
    :class:`dirtyjson.Error` at the position where it occurs.  By default
    all features are allowed, which keeps ``load`` as permissive as ever.
    """
    return loads(fp.read(), encoding, parse_float, parse_int, parse_constant,
                 search_for_first_object, allowed_features=allowed_features)


def loads(s, encoding=None, parse_float=None, parse_int=None,
          parse_constant=None, search_for_first_object=False, start_index=0,
          allowed_features=None):
    """Deserialize ``s`` (a ``str`` or ``unicode`` instance containing a JSON
    document) to a Python object.

    *encoding* determines the encoding used to interpret any
    :class:`str` objects decoded by this instance (``'utf-8'`` by
    default).  It has no effect when decoding :class:`unicode` objects.

    Note that currently only encodings that are a superset of ASCII work,
    strings of other encodings should be passed in as :class:`unicode`.

    *parse_float*, if specified, will be called with the string of every
    JSON float to be decoded.  By default, this is equivalent to
    ``float(num_str)``. This can be used to use another data type or parser
    for JSON floats (e.g. :class:`decimal.Decimal`).

    *parse_int*, if specified, will be called with the string of every
    JSON int to be decoded.  By default, this is equivalent to
    ``int(num_str)``.  This can be used to use another data type or parser
    for JSON integers (e.g. :class:`float`).

    *parse_constant*, if specified, will be called with one of the
    following strings: ``'-Infinity'``, ``'Infinity'``, ``'NaN'``.  This
    can be used to raise an exception if invalid JSON numbers are
    encountered.

    *allowed_features*, if specified, is an iterable of lenient syntax
    feature names (see :mod:`dirtyjson.features`) that the document is
    permitted to use.  Any other lenient feature raises
    :class:`dirtyjson.Error` at the position where it occurs.  By default
    all features are allowed, which keeps ``loads`` as permissive as ever.
    """
    d = DirtyJSONLoader(s, encoding, parse_float, parse_int, parse_constant,
                        allowed_features=allowed_features)
    return d.decode(search_for_first_object, start_index)


def load_with_features(fp, encoding=None, parse_float=None, parse_int=None,
                       parse_constant=None, search_for_first_object=False,
                       allowed_features=None):
    """Like :func:`load`, but return a ``(obj, features)`` tuple where
    *features* is a list of :class:`FeatureUse` records, in document order,
    describing every lenient (non-standard JSON) syntax feature the
    document relied on and where each one occurs."""
    return loads_with_features(fp.read(), encoding, parse_float, parse_int,
                               parse_constant, search_for_first_object,
                               allowed_features=allowed_features)


def loads_with_features(s, encoding=None, parse_float=None, parse_int=None,
                        parse_constant=None, search_for_first_object=False,
                        start_index=0, allowed_features=None):
    """Like :func:`loads`, but return a ``(obj, features)`` tuple where
    *features* is a list of :class:`FeatureUse` records, in document order,
    describing every lenient (non-standard JSON) syntax feature the
    document relied on and where each one occurs."""
    d = DirtyJSONLoader(s, encoding, parse_float, parse_int, parse_constant,
                        allowed_features=allowed_features)
    return d.decode(search_for_first_object, start_index), d.features_used
