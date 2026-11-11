# -*- coding: utf-8 -*-
from __future__ import absolute_import
from unittest import TestCase

import dirtyjson

BACKSLASH = '\\'


class TestIdentifierKeys(TestCase):
    def test_ascii_bare_key(self):
        self.assertEqual(dirtyjson.loads('{key: 1}'), {'key': 1})
        self.assertEqual(dirtyjson.loads('{_key: 1}'), {'_key': 1})
        self.assertEqual(dirtyjson.loads('{$key1: 1}'), {'$key1': 1})

    def test_dollar_is_allowed_even_though_python_rejects_it(self):
        # The declared grammar accepts ``$``; this must not depend on the
        # host machine's ``str.isidentifier()`` result (which rejects ``$``).
        self.assertFalse('$key'.isidentifier())
        self.assertEqual(dirtyjson.loads('{$key: 1}'), {'$key': 1})

    def test_non_ascii_letter_bare_key(self):
        self.assertEqual(dirtyjson.loads(u'{名前: 1}'), {u'名前': 1})
        self.assertEqual(dirtyjson.loads(u'{café_2: 1}'),
                         {u'café_2': 1})

    def test_combining_mark_is_continuation_only(self):
        # U+0301 COMBINING ACUTE ACCENT has category Mn: it may continue an
        # identifier but may not start one.
        self.assertEqual(dirtyjson.loads(u'{á: 1}'),
                         {u'á': 1})
        self.assertRaises(dirtyjson.Error, dirtyjson.loads, u'{́: 1}')

    def test_digits_and_punctuation_cannot_start_key(self):
        for doc in ('{1a: 1}', '{123: 1}', '{-a: 1}', u'{。: 1}'):
            self.assertRaises(dirtyjson.Error, dirtyjson.loads, doc)

    def test_punctuation_terminates_bare_key(self):
        self.assertRaises(dirtyjson.Error, dirtyjson.loads, '{a-b: 1}')

    def test_unicode_escape_in_bare_key(self):
        self.assertEqual(
            dirtyjson.loads('{a' + BACKSLASH + 'u0062: 1}'), {'ab': 1})
        self.assertEqual(
            dirtyjson.loads('{' + BACKSLASH + 'u005fx: 1}'), {'_x': 1})
        self.assertEqual(
            dirtyjson.loads('{a' + BACKSLASH + 'u0024b: 1}'), {'a$b': 1})

    def test_surrogate_pair_escape_in_bare_key(self):
        # U+1D400 MATHEMATICAL BOLD CAPITAL A is a letter (Lu), written
        # as an escaped surrogate pair inside the unquoted key.
        doc = '{' + BACKSLASH + 'ud835' + BACKSLASH + 'udc00: 1}'
        self.assertEqual(dirtyjson.loads(doc), {u'\U0001d400': 1})

    def test_astral_symbol_escape_rejected_by_grammar(self):
        # U+1D11E MUSICAL SYMBOL G CLEF is a symbol (So), not a letter.
        doc = '{' + BACKSLASH + 'ud834' + BACKSLASH + 'udd1e: 1}'
        self.assertRaises(dirtyjson.Error, dirtyjson.loads, doc)

    def test_escape_result_is_grammar_checked(self):
        # U+002E FULL STOP and U+0020 SPACE are not identifier characters.
        self.assertRaises(dirtyjson.Error, dirtyjson.loads,
                          '{a' + BACKSLASH + 'u002E: 1}')
        self.assertRaises(dirtyjson.Error, dirtyjson.loads,
                          '{' + BACKSLASH + 'u0020x: 1}')

    def test_invalid_escape_in_bare_key(self):
        self.assertRaises(dirtyjson.Error, dirtyjson.loads, r'{a\q: 1}')
        self.assertRaises(dirtyjson.Error, dirtyjson.loads,
                          '{a' + BACKSLASH)

    def test_quoted_keys_keep_working(self):
        self.assertEqual(dirtyjson.loads('{"a-b": 1}'), {'a-b': 1})
        self.assertEqual(dirtyjson.loads("{'a-b': 1}"), {'a-b': 1})
        self.assertEqual(dirtyjson.loads('{"123": 1}'), {'123': 1})
        self.assertEqual(dirtyjson.loads(u'{"€": 1}'), {u'€': 1})


class TestDuplicateKeys(TestCase):
    DOC = r'{"ab": 1, ab: 2}'

    def test_default_policy_uses_last_value(self):
        self.assertEqual(dirtyjson.loads(self.DOC), {'ab': 2})

    def test_use_last_policy(self):
        result = dirtyjson.loads(
            self.DOC,
            duplicate_key_policy=dirtyjson.DUPLICATE_KEY_USE_LAST)
        self.assertEqual(result, {'ab': 2})

    def test_use_first_policy(self):
        result = dirtyjson.loads(
            self.DOC,
            duplicate_key_policy=dirtyjson.DUPLICATE_KEY_USE_FIRST)
        self.assertEqual(result, {'ab': 1})

    def test_error_policy_raises_on_colliding_spellings(self):
        with self.assertRaises(dirtyjson.Error) as cm:
            dirtyjson.loads(
                self.DOC,
                duplicate_key_policy=dirtyjson.DUPLICATE_KEY_ERROR)
        self.assertEqual(cm.exception.lineno, 1)
        self.assertEqual(cm.exception.pos, self.DOC.rindex('ab'))

    def test_three_spellings_of_one_key_are_one_key(self):
        doc = r'{"ab": 1, ab: 2, ab: 3}'
        self.assertEqual(dirtyjson.loads(doc), {'ab': 3})
        self.assertEqual(
            dirtyjson.loads(
                doc,
                duplicate_key_policy=dirtyjson.DUPLICATE_KEY_USE_FIRST),
            {'ab': 1})
        self.assertRaises(
            dirtyjson.Error, dirtyjson.loads, doc,
            duplicate_key_policy=dirtyjson.DUPLICATE_KEY_ERROR)

    def test_distinct_keys_are_not_duplicates(self):
        result = dirtyjson.loads(
            u'{a: 1, b: 2, 名前: 3}',
            duplicate_key_policy=dirtyjson.DUPLICATE_KEY_ERROR)
        self.assertEqual(result, {'a': 1, 'b': 2, u'名前': 3})

    def test_policy_applies_per_object(self):
        self.assertRaises(
            dirtyjson.Error, dirtyjson.loads,
            '{"o": {"a": 1, "a": 2}}',
            duplicate_key_policy=dirtyjson.DUPLICATE_KEY_ERROR)

    def test_use_first_keeps_first_attributes(self):
        first = dirtyjson.loads(
            self.DOC,
            duplicate_key_policy=dirtyjson.DUPLICATE_KEY_USE_FIRST)
        last = dirtyjson.loads(self.DOC)
        self.assertEqual(first.attributes('ab').value.column,
                         self.DOC.index('1') + 1)
        self.assertEqual(last.attributes('ab').value.column,
                         self.DOC.index('2') + 1)

    def test_invalid_policy_rejected(self):
        self.assertRaises(
            ValueError, dirtyjson.loads, '{}',
            duplicate_key_policy='not_a_policy')

    def test_policy_accepted_by_load(self):
        from dirtyjson.compat import StringIO
        result = dirtyjson.load(
            StringIO(self.DOC),
            duplicate_key_policy=dirtyjson.DUPLICATE_KEY_USE_FIRST)
        self.assertEqual(result, {'ab': 1})
