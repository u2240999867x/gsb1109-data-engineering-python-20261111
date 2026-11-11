from __future__ import absolute_import
import sys
from unittest import TestCase

import dirtyjson
from dirtyjson.compat import StringIO
from dirtyjson.features import (
    ALL_FEATURES, FeatureUse, SINGLE_QUOTED_STRINGS, COMMENTS,
    TRAILING_COMMAS, UNQUOTED_KEYS, HEXADECIMAL_NUMBERS, OCTAL_NUMBERS,
    EXPRESSIONS, UNESCAPED_CONTROL_CHARACTERS)


def used(doc, **kw):
    obj, features = dirtyjson.loads_with_features(doc, **kw)
    return obj, [(f.feature, f.pos) for f in features]


class TestFeatureReporting(TestCase):
    def test_clean_json_reports_no_features(self):
        obj, features = dirtyjson.loads_with_features(
            '{"a": [1, 2.5, "x", true, false, null, 0, 10], "b": {}}')
        self.assertEqual(obj, {'a': [1, 2.5, 'x', True, False, None, 0, 10], 'b': {}})
        self.assertEqual(features, [])

    def test_single_quoted_strings(self):
        obj, features = used("{'a': 'b'}")
        self.assertEqual(obj, {'a': 'b'})
        self.assertEqual(features, [(SINGLE_QUOTED_STRINGS, 1),
                                    (SINGLE_QUOTED_STRINGS, 6)])

    def test_single_quote_inside_double_quoted_string_is_not_a_feature(self):
        # a lone apostrophe and an escaped \' inside a "..." string are
        # ordinary string content, not single-quoted strings
        obj, features = used('["it\'s", "\\\'"]')
        self.assertEqual(obj, ["it's", "'"])
        self.assertEqual(features, [])

    def test_comments(self):
        doc = '// line\n{ /* block */ "a": 1}'
        obj, features = used(doc)
        self.assertEqual(obj, {'a': 1})
        self.assertEqual(features, [(COMMENTS, doc.index('//')),
                                    (COMMENTS, doc.index('/*'))])

    def test_trailing_commas(self):
        doc = '{"a": [1, 2,], "b": 3,}'
        obj, features = used(doc)
        self.assertEqual(obj, {'a': [1, 2], 'b': 3})
        self.assertEqual(features, [(TRAILING_COMMAS, doc.index(',]')),
                                    (TRAILING_COMMAS, doc.index(',}'))])

    def test_no_trailing_comma_for_empty_containers(self):
        obj, features = used('{"a": [], "b": {}}')
        self.assertEqual(features, [])

    def test_unquoted_keys(self):
        obj, features = used('{key_1: 1, "$two": 2}')
        self.assertEqual(obj, {'key_1': 1, '$two': 2})
        self.assertEqual(features, [(UNQUOTED_KEYS, 1)])

    def test_hex_and_octal_numbers(self):
        doc = '[0x40, -0x40, 077, 0, 10, 0.5, 1e2]'
        obj, features = used(doc)
        self.assertEqual(obj, [64, -64, 63, 0, 10, 0.5, 100.0])
        self.assertEqual(features, [(HEXADECIMAL_NUMBERS, doc.index('0x40')),
                                    (HEXADECIMAL_NUMBERS, doc.index('-0x40')),
                                    (OCTAL_NUMBERS, doc.index('077'))])

    def test_expressions(self):
        obj, features = used('[1+2]')
        self.assertEqual(obj, [3])
        self.assertEqual(features, [(EXPRESSIONS, 1)])

    def test_unescaped_control_characters(self):
        doc = '["a\tb"]'
        obj, features = used(doc)
        self.assertEqual(obj, ['a\tb'])
        self.assertEqual(features, [(UNESCAPED_CONTROL_CHARACTERS, doc.index('\t'))])

    def test_whitespace_between_tokens_is_not_a_feature(self):
        obj, features = used('[1,\t\n 2]')
        self.assertEqual(obj, [1, 2])
        self.assertEqual(features, [])

    def test_features_reported_in_document_order_with_line_and_column(self):
        doc = '// c\n{\n key: 0x1,\n}'
        obj, features = dirtyjson.loads_with_features(doc)
        self.assertEqual([(f.feature, f.pos, f.line, f.column) for f in features], [
            (COMMENTS, 0, 1, 1),
            (UNQUOTED_KEYS, 8, 3, 2),
            (HEXADECIMAL_NUMBERS, 13, 3, 7),
            (TRAILING_COMMAS, 16, 3, 10),
        ])

    def test_feature_use_equality_and_repr(self):
        obj, features = dirtyjson.loads_with_features("'a'")
        self.assertEqual(features, [FeatureUse(SINGLE_QUOTED_STRINGS, 0, 1, 1)])
        self.assertEqual(repr(features[0]),
                         "FeatureUse('single_quoted_strings', pos=0, line=1, column=1)")

    def test_load_with_features_reads_stream(self):
        obj, features = dirtyjson.load_with_features(StringIO("{'a': 1}"))
        self.assertEqual(obj, {'a': 1})
        self.assertEqual([(f.feature, f.pos) for f in features],
                         [(SINGLE_QUOTED_STRINGS, 1)])

    def test_loader_exposes_features_used(self):
        from dirtyjson.loader import DirtyJSONLoader
        d = DirtyJSONLoader('{a: 1}')
        d.decode()
        self.assertEqual([(f.feature, f.pos) for f in d.features_used],
                         [(UNQUOTED_KEYS, 1)])


class TestAllowedFeatures(TestCase):
    def test_default_loads_stays_fully_lenient(self):
        doc = "// c\n{key: 'v', list: [1,], hex: 0x10, oct: 077, expr: 1+2, ctrl: 'a\tb'}"
        obj = dirtyjson.loads(doc)
        self.assertEqual(obj, {'key': 'v', 'list': [1], 'hex': 16, 'oct': 63,
                               'expr': 3, 'ctrl': 'a\tb'})

    def test_strict_json_passes_with_nothing_allowed(self):
        self.assertEqual(
            dirtyjson.loads('{"a": [1, 2.5, "x"]}', allowed_features=[]),
            {'a': [1, 2.5, 'x']})

    def test_disallowed_feature_raises_at_original_position(self):
        try:
            dirtyjson.loads('{\n  \'a\': 1}', allowed_features=[])
        except dirtyjson.Error:
            err = sys.exc_info()[1]
        else:
            self.fail('Expected dirtyjson.Error')
        self.assertEqual(err.pos, 4)
        self.assertEqual(err.lineno, 2)
        self.assertEqual(err.colno, 3)
        self.assertIn(SINGLE_QUOTED_STRINGS, err.msg)

    def test_allowed_subset_permits_only_those_features(self):
        doc = "/* c */ {'a': 1}"
        self.assertEqual(
            dirtyjson.loads(doc, allowed_features=[COMMENTS, SINGLE_QUOTED_STRINGS]),
            {'a': 1})
        try:
            dirtyjson.loads(doc, allowed_features=[COMMENTS])
        except dirtyjson.Error:
            err = sys.exc_info()[1]
        else:
            self.fail('Expected dirtyjson.Error')
        self.assertEqual(err.pos, 9)

    def test_each_feature_can_be_disabled_individually(self):
        cases = [
            ("'a'", SINGLE_QUOTED_STRINGS),
            ('// c\n1', COMMENTS),
            ('/* c */ 1', COMMENTS),
            ('[1,]', TRAILING_COMMAS),
            ('{"a": 1,}', TRAILING_COMMAS),
            ('{a: 1}', UNQUOTED_KEYS),
            ('0x10', HEXADECIMAL_NUMBERS),
            ('077', OCTAL_NUMBERS),
            ('[1+2]', EXPRESSIONS),
            ('["a\tb"]', UNESCAPED_CONTROL_CHARACTERS),
        ]
        for doc, feature in cases:
            allowed = [f for f in ALL_FEATURES if f != feature]
            try:
                dirtyjson.loads(doc, allowed_features=allowed)
            except dirtyjson.Error:
                err = sys.exc_info()[1]
            else:
                self.fail('Expected dirtyjson.Error for %r without %s' % (doc, feature))
            self.assertIn(feature, err.msg)
            # and the same document parses when the feature is allowed
            dirtyjson.loads(doc, allowed_features=allowed + [feature])

    def test_allowed_features_accepts_a_single_feature_name(self):
        self.assertEqual(dirtyjson.loads('// c\n1', allowed_features=COMMENTS), 1)

    def test_unknown_feature_name_rejected(self):
        for allowed in (['bogus'], [COMMENTS, 'bogus']):
            try:
                dirtyjson.loads('1', allowed_features=allowed)
            except ValueError:
                pass
            else:
                self.fail('Expected ValueError for %r' % (allowed,))

    def test_load_passes_allowed_features_through(self):
        sio = StringIO('{a: 1}')
        self.assertEqual(dirtyjson.load(sio, allowed_features=[UNQUOTED_KEYS]),
                         {'a': 1})
        try:
            dirtyjson.load(StringIO('{a: 1}'), allowed_features=[])
        except dirtyjson.Error:
            pass
        else:
            self.fail('Expected dirtyjson.Error')

    def test_disallowed_trailing_comma_reports_comma_position(self):
        doc = '[1, 2,]'
        try:
            dirtyjson.loads(doc, allowed_features=[])
        except dirtyjson.Error:
            err = sys.exc_info()[1]
        else:
            self.fail('Expected dirtyjson.Error')
        self.assertEqual(err.pos, doc.index(',]'))

    def test_disallowed_comment_reports_comment_position(self):
        doc = '[1, /* c */ 2]'
        try:
            dirtyjson.loads(doc, allowed_features=[])
        except dirtyjson.Error:
            err = sys.exc_info()[1]
        else:
            self.fail('Expected dirtyjson.Error')
        self.assertEqual(err.pos, doc.index('/*'))
