"""Position contract tests for dirtyjson.

These tests pin down *what unit* the reported line/column positions use and
guarantee that a position can be walked back to the exact spot in the original
document. They deliberately cover non-ASCII content (Chinese), CRLF line
endings, a byte-order mark, nested arrays and malformed values, fed in both as
``str`` and as UTF-8 ``bytes``.

Coordinate contract (current implementation, see :class:`dirtyjson.loader.DirtyJSONLoader`)
------------------------------------------------------------------------------------------------

* All positions are **character offsets into the decoded Unicode text**, never
  raw byte offsets. On Python 3 a ``bytes`` document is decoded with
  ``content.decode()`` (UTF-8 by default) in ``DirtyJSONLoader.__init__``
  *before* any position is computed, so feeding the same document as ``str`` or
  as UTF-8 ``bytes`` yields byte-for-byte identical line/column numbers.
* Lines are 1-based; columns are 1-based characters counted from the start of
  the current line. The current line starts immediately after the most recent
  ``'\\n'``. With CRLF (``'\\r\\n'``) the trailing ``'\\r'`` therefore occupies
  the last column of the line; the ``'\\n'`` itself is the line separator.
* A key position points at the key's first character -- the opening quote for
  a quoted key, the first identifier character for an unquoted key. A value
  position points at the value token's first character (the opening quote of a
  string, the first digit, ``[``/``{``, ``t``/``f``/``n``, or the offending
  character for a malformed value).
* ``dirtyjson.Error.pos`` is the same character index used internally;
  ``lineno``/``colno`` are derived from it and ``Error.doc`` is always the
  decoded ``str`` even when bytes were supplied.

Byte <-> character conversion boundary
--------------------------------------

Because coordinates are character based, a column/``pos`` cannot be used to
index the original UTF-8 byte string once non-ASCII characters appear. A CJK
character is one character but three UTF-8 bytes, so every position after it
shifts relative to the byte stream. Callers that need a byte offset must
convert on the *decoded text*, e.g.::

    byte_offset = len(decoded_text[:char_index].encode('utf-8'))

The BOM is an additional boundary: dirtyjson does not strip a leading
``'\\ufeff'``. It is treated as an unexpected character and raises at line 1
column 1. To consume a BOM, decode bytes with ``utf-8-sig`` (or otherwise
remove the mark) *before* calling :func:`dirtyjson.loads`.
"""
from unittest import TestCase

import dirtyjson
from dirtyjson.compat import u, b, binary_type, text_type


BOM = u('﻿')


def resolve(doc, line, column):
    """Return the character at a dirtyjson (line, column) in decoded text.

    Mirrors the loader's rule: a line starts right after the last '\\n', and
    columns are 1-based characters. This is the authoritative round-trip used
    by the contract tests.
    """
    line_starts = [0]
    for index, char in enumerate(doc):
        if char == '\n':
            line_starts.append(index + 1)
    line_start = line_starts[line - 1]
    return doc[line_start + column - 1]


class TestPositionContract(TestCase):
    def assert_resolves(self, doc, position, expected_char):
        self.assertEqual(resolve(doc, position.line, position.column),
                         expected_char)

    # ------------------------------------------------------------------ str/bytes parity

    def test_str_and_utf8_bytes_report_identical_positions(self):
        text = u('{"名": "值", "b": 2}')
        from_str = dirtyjson.loads(text)
        from_bytes = dirtyjson.loads(text.encode('utf-8'))

        for key in from_str:
            sp = from_str.attributes(key)
            bp = from_bytes.attributes(key)
            self.assertEqual((sp.key.line, sp.key.column),
                             (bp.key.line, bp.key.column))
            self.assertEqual((sp.value.line, sp.value.column),
                             (bp.value.line, bp.value.column))

    def test_error_after_non_ascii_is_identical_for_str_and_bytes(self):
        text = u('{"名": "值", "b": x}')
        errors = []
        for source in (text, text.encode('utf-8')):
            with self.assertRaises(dirtyjson.Error) as ctx:
                dirtyjson.loads(source)
            errors.append(ctx.exception)

        str_err, bytes_err = errors
        # Coordinates are character based, so they must agree across inputs...
        self.assertEqual((str_err.pos, str_err.lineno, str_err.colno),
                         (bytes_err.pos, bytes_err.lineno, bytes_err.colno))
        # ...and the byte input's error doc is the decoded text.
        self.assertIsInstance(bytes_err.doc, text_type)
        self.assertNotIsInstance(bytes_err.doc, binary_type)
        self.assertEqual(bytes_err.doc, text)

    # ------------------------------------------------------- positions round-trip to source

    def test_object_positions_round_trip_to_source_characters(self):
        doc = u('{"名": "值", "b": 2}')
        obj = dirtyjson.loads(doc)

        first = obj.attributes(u('名'))
        self.assertEqual((first.key.line, first.key.column), (1, 2))
        self.assertEqual((first.value.line, first.value.column), (1, 7))
        self.assert_resolves(doc, first.key, u('"'))      # opening quote of key
        self.assert_resolves(doc, first.value, u('"'))    # opening quote of value

        second = obj.attributes(u('b'))
        self.assertEqual((second.key.line, second.key.column), (1, 12))
        self.assertEqual((second.value.line, second.value.column), (1, 17))
        self.assert_resolves(doc, second.key, u('"'))
        self.assert_resolves(doc, second.value, u('2'))

    def test_nested_array_positions_round_trip(self):
        doc = u('["甲", ["乙", "丙"], "丁"]')
        outer = dirtyjson.loads(doc)

        expected_outer = [
            (0, (1, 2), u('"')),    # "甲"
            (1, (1, 7), u('[')),    # nested array
            (2, (1, 19), u('"')),   # "丁"
        ]
        for index, (line, column), char in expected_outer:
            pos = outer.attributes(index)
            self.assertEqual((pos.line, pos.column), (line, column))
            self.assert_resolves(doc, pos, char)

        inner = outer[1]
        expected_inner = [
            (0, (1, 8), u('"')),     # "乙"
            (1, (1, 13), u('"')),    # "丙"
        ]
        for index, (line, column), char in expected_inner:
            pos = inner.attributes(index)
            self.assertEqual((pos.line, pos.column), (line, column))
            self.assert_resolves(doc, pos, char)

    def test_chinese_positions_across_crlf_lines(self):
        # CRLF: '\r' is the final column of each line; columns count characters.
        doc = u('{\r\n  "名": "值",\r\n  "b": 2\r\n}')
        obj = dirtyjson.loads(doc)

        chinese = obj.attributes(u('名'))
        self.assertEqual((chinese.key.line, chinese.key.column), (2, 3))
        self.assertEqual((chinese.value.line, chinese.value.column), (2, 8))
        self.assert_resolves(doc, chinese.key, u('"'))
        self.assert_resolves(doc, chinese.value, u('"'))

        plain = obj.attributes(u('b'))
        self.assertEqual((plain.key.line, plain.key.column), (3, 3))
        self.assertEqual((plain.value.line, plain.value.column), (3, 8))
        self.assert_resolves(doc, plain.key, u('"'))
        self.assert_resolves(doc, plain.value, u('2'))

        # The same document handed over as bytes must keep identical coordinates.
        from_bytes = dirtyjson.loads(doc.encode('utf-8'))
        self.assertEqual(
            (from_bytes.attributes(u('名')).key.line,
             from_bytes.attributes(u('名')).key.column),
            (2, 3))

    # ------------------------------------------------------------- malformed value locations

    def test_malformed_value_position_points_at_offending_character(self):
        doc = u('{"名": "值", "b": x}')
        with self.assertRaises(dirtyjson.Error) as ctx:
            dirtyjson.loads(doc)
        err = ctx.exception

        self.assertEqual(err.pos, 16)
        self.assertEqual((err.lineno, err.colno), (1, 17))
        self.assertEqual(err.doc[err.pos], u('x'))
        self.assertEqual(resolve(err.doc, err.lineno, err.colno), u('x'))

    def test_malformed_array_value_on_later_line(self):
        doc = u('[\n  "甲",\n  bad\n]')
        with self.assertRaises(dirtyjson.Error) as ctx:
            dirtyjson.loads(doc)
        err = ctx.exception

        self.assertEqual((err.lineno, err.colno), (3, 3))
        self.assertEqual(resolve(err.doc, err.lineno, err.colno), u('b'))

    # -------------------------------------------------------------------------- BOM boundary

    def test_bom_is_not_stripped_and_is_reported_at_line_1_column_1(self):
        clean = u('{"a": 1}')
        # A leading BOM arrives either explicitly in a str or inside UTF-8 bytes
        # (a utf-8-sig encoded payload still contains the mark when decoded as
        # plain utf-8, which is what the loader does).
        for source in (BOM + clean,
                       (BOM + clean).encode('utf-8'),
                       clean.encode('utf-8-sig')):
            with self.assertRaises(dirtyjson.Error) as ctx:
                dirtyjson.loads(source)
            err = ctx.exception
            self.assertEqual(err.pos, 0)
            self.assertEqual((err.lineno, err.colno), (1, 1))
            self.assertEqual(err.doc[0], BOM)

    def test_bom_can_be_consumed_by_decoding_utf8_sig_first(self):
        clean = u('{"a": 1}')
        decoded = clean.encode('utf-8-sig').decode('utf-8-sig')
        obj = dirtyjson.loads(decoded)
        value_pos = obj.attributes(u('a')).value
        self.assertEqual((value_pos.line, value_pos.column), (1, 7))
        self.assert_resolves(decoded, value_pos, u('1'))

    # ------------------------------------------------- byte vs character coordinate boundary

    def test_reported_column_is_character_not_utf8_byte_offset(self):
        doc = u('{"名": "值", "b": 2}')
        raw = doc.encode('utf-8')
        obj = dirtyjson.loads(doc)

        key_pos = obj.attributes(u('b')).key
        char_index = key_pos.column - 1            # single line, 1-based column
        self.assertEqual(doc[char_index], u('"'))

        byte_offset = len(doc[:char_index].encode('utf-8'))
        # Each CJK character costs one char but three UTF-8 bytes: the two
        # characters 名 and 值 push the byte offset four positions past the
        # character index. This is the documented conversion boundary.
        self.assertEqual(char_index, 11)
        self.assertEqual(byte_offset, 15)
        self.assertNotEqual(char_index, byte_offset)

        # The character coordinate must not be used directly on the byte string:
        # raw[char_index] lands inside a multi-byte sequence, not the quote.
        self.assertNotEqual(raw[char_index], b('"')[0])
        self.assertEqual(raw[byte_offset], b('"')[0])
