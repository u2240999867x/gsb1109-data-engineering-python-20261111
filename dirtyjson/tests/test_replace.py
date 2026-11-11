from __future__ import absolute_import
from unittest import TestCase

import dirtyjson
from dirtyjson import replace_value, AmbiguousPathError


CONFIG = """// service configuration -- do not reformat
{
    name: 'audit-worker',   // service name
    retries: 3,
    database: {
        host: "db.internal",
        timeout: 30,        // seconds; audited 2026-11
    },
    features: ['scan', 'report'],
}
"""


def assert_untouched(testcase, original, updated, replaced_span):
    prefix = original[:original.index(replaced_span)]
    suffix = original[original.index(replaced_span) + len(replaced_span):]
    testcase.assertTrue(updated.startswith(prefix))
    testcase.assertTrue(updated.endswith(suffix))
    testcase.assertEqual(
        updated, prefix + updated[len(prefix):len(updated) - len(suffix)] + suffix)


class TestReplaceValue(TestCase):
    def test_scalar_replacement_preserves_everything_else(self):
        updated = replace_value(CONFIG, ['database', 'timeout'], 60)
        self.assertEqual(dirtyjson.loads(updated)['database']['timeout'], 60)
        assert_untouched(self, CONFIG, updated, '30')
        # only the two digits of the old value changed
        self.assertEqual(len(updated), len(CONFIG))
        self.assertEqual(CONFIG.replace('timeout: 30', 'timeout: 60'), updated)

    def test_string_scalar_replacement(self):
        updated = replace_value(CONFIG, ['name'], 'billing-worker')
        self.assertEqual(dirtyjson.loads(updated)['name'], 'billing-worker')
        assert_untouched(self, CONFIG, updated, "'audit-worker'")

    def test_array_element_replacement(self):
        updated = replace_value(CONFIG, ['features', 1], 'archive')
        self.assertEqual(dirtyjson.loads(updated)['features'], ['scan', 'archive'])
        assert_untouched(self, CONFIG, updated, "'report'")

    def test_whole_subobject_replacement(self):
        updated = replace_value(CONFIG, ['database'],
                                {'host': 'db2.internal', 'timeout': 5})
        database = dirtyjson.loads(updated)['database']
        self.assertEqual(database['host'], 'db2.internal')
        self.assertEqual(database['timeout'], 5)
        self.assertEqual(dirtyjson.loads(updated)['retries'], 3)
        prefix = CONFIG[:CONFIG.index('{', CONFIG.index('database'))]
        self.assertTrue(updated.startswith(prefix.rstrip()))

    def test_raw_subobject_replacement_keeps_custom_formatting(self):
        raw = "{\n        host: 'db3',\n        timeout: 10,\n    }"
        updated = replace_value(CONFIG, ['database'], raw, raw=True)
        self.assertIn(raw, updated)
        database = dirtyjson.loads(updated)['database']
        self.assertEqual(database['host'], 'db3')
        self.assertEqual(database['timeout'], 10)

    def test_duplicate_keys_require_explicit_occurrence(self):
        content = '{timeout: 1, other: 9, timeout: 2}'
        with self.assertRaises(AmbiguousPathError):
            replace_value(content, ['timeout'], 5)
        # the document is unchanged after the refused edit
        self.assertEqual(dirtyjson.loads(content)['timeout'], 2)

    def test_duplicate_keys_explicit_occurrence(self):
        content = '{timeout: 1, other: 9, timeout: 2}'
        first = replace_value(content, ['timeout'], 10, occurrence=0)
        self.assertEqual(first, '{timeout: 10, other: 9, timeout: 2}')
        second = replace_value(content, ['timeout'], 20, occurrence=1)
        self.assertEqual(second, '{timeout: 1, other: 9, timeout: 20}')
        self.assertEqual(dirtyjson.loads(second)['timeout'], 20)

    def test_duplicate_keys_occurrence_mapping(self):
        content = '{a: {timeout: 1}, b: {timeout: 2, timeout: 3}}'
        updated = replace_value(content, ['b', 'timeout'], 8,
                                occurrence={'timeout': 1})
        self.assertEqual(updated, '{a: {timeout: 1}, b: {timeout: 2, timeout: 8}}')

    def test_nested_path_through_duplicate_key(self):
        content = '{server: {timeout: 1}, server: {timeout: 2}}'
        updated = replace_value(content, ['server', 'timeout'], 7,
                                occurrence={'server': 0})
        self.assertEqual(updated, '{server: {timeout: 7}, server: {timeout: 2}}')

    def test_occurrence_out_of_range(self):
        content = '{timeout: 1, timeout: 2}'
        with self.assertRaises(IndexError):
            replace_value(content, ['timeout'], 5, occurrence=2)

    def test_missing_key(self):
        with self.assertRaises(KeyError):
            replace_value(CONFIG, ['database', 'missing'], 1)

    def test_root_replacement(self):
        updated = replace_value('  {a: 1}  ', [], {'b': 2})
        self.assertEqual(dirtyjson.loads(updated), {'b': 2})

    def test_roundtrip_business_value(self):
        updated = replace_value(CONFIG, ['database', 'timeout'], 45)
        reloaded = dirtyjson.loads(updated)
        self.assertEqual(reloaded['database']['timeout'], 45)
        self.assertEqual(reloaded['name'], 'audit-worker')
        self.assertEqual(reloaded['retries'], 3)
        self.assertEqual(reloaded['features'], ['scan', 'report'])
