from __future__ import absolute_import
import json
from unittest import TestCase

import dirtyjson
from dirtyjson import MigrationError, MigrationIssue


class TestAnalyze(TestCase):
    def test_clean_document_has_no_issues(self):
        self.assertEqual(dirtyjson.analyze('{"a": 1, "b": [true, null]}'), [])

    def test_nonfinite_values_are_reported_with_paths(self):
        issues = dirtyjson.analyze(
            '{"a": NaN, "b": [Infinity, {"c": -Infinity}], "d": 1.5}')
        kinds = [issue.kind for issue in issues]
        self.assertEqual(kinds, ['nan', 'infinity', '-infinity'])
        paths = [issue.path for issue in issues]
        self.assertEqual(paths, ['$["a"]', '$["b"][0]', '$["b"][1]["c"]'])

    def test_duplicate_keys_are_reported_not_collapsed(self):
        issues = dirtyjson.analyze('{"a": 1, "b": 2, "a": 3, "a": 4}')
        self.assertEqual(len(issues), 1)
        issue = issues[0]
        self.assertEqual(issue.kind, 'duplicate_key')
        self.assertEqual(issue.key, 'a')
        self.assertEqual(issue.count, 3)
        self.assertEqual(issue.path, '$["a"]')

    def test_nested_duplicate_keys(self):
        issues = dirtyjson.analyze('{"x": {"y": 1, "y": 2}}')
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].path, '$["x"]["y"]')

    def test_source_text_is_not_modified(self):
        source = '{"a": NaN, "a": 1}'
        dirtyjson.analyze(source)
        self.assertEqual(source, '{"a": NaN, "a": 1}')


class TestMigrate(TestCase):
    def test_clean_document_roundtrips_through_stdlib(self):
        source = '{"a": 1, "b": [1.5, "x", true, null], "c": {"d": 2}}'
        out = dirtyjson.migrate(source)
        self.assertEqual(json.loads(out),
                         {"a": 1, "b": [1.5, "x", True, None], "c": {"d": 2}})

    def test_unhandled_nonfinite_raises_with_issues(self):
        try:
            dirtyjson.migrate('{"a": NaN}')
        except MigrationError as exc:
            self.assertEqual(len(exc.issues), 1)
            self.assertEqual(exc.issues[0].kind, 'nan')
        else:
            self.fail('MigrationError not raised')

    def test_unhandled_duplicate_key_raises_with_issues(self):
        try:
            dirtyjson.migrate('{"a": 1, "a": 2}')
        except MigrationError as exc:
            self.assertEqual(len(exc.issues), 1)
            self.assertEqual(exc.issues[0].kind, 'duplicate_key')
        else:
            self.fail('MigrationError not raised')

    def test_nonfinite_callable_replacement(self):
        out = dirtyjson.migrate(
            '{"a": NaN, "b": Infinity, "c": -Infinity}',
            on_nonfinite=lambda issue: {'kind': issue.kind})
        self.assertEqual(json.loads(out), {
            "a": {"kind": "nan"},
            "b": {"kind": "infinity"},
            "c": {"kind": "-infinity"},
        })

    def test_nonfinite_plain_value_replacement(self):
        out = dirtyjson.migrate('[NaN, Infinity]', on_nonfinite="n/a")
        self.assertEqual(json.loads(out), ["n/a", "n/a"])

    def test_nonfinite_replacement_must_be_finite(self):
        self.assertRaises(
            MigrationError,
            dirtyjson.migrate, '{"a": NaN}', on_nonfinite=lambda issue: 1e999)

    def test_duplicate_first_and_last(self):
        source = '{"a": 1, "a": 2, "a": 3}'
        self.assertEqual(json.loads(dirtyjson.migrate(
            source, on_duplicate='first')), {"a": 1})
        self.assertEqual(json.loads(dirtyjson.migrate(
            source, on_duplicate='last')), {"a": 3})

    def test_duplicate_callable_gets_all_values_in_order(self):
        seen = []

        def pick(key, values):
            seen.append((key, list(values)))
            return values[1]

        out = dirtyjson.migrate('{"a": 1, "a": 2, "a": 3}',
                                on_duplicate=pick)
        self.assertEqual(seen, [('a', [1, 2, 3])])
        self.assertEqual(json.loads(out), {"a": 2})

    def test_duplicate_values_are_converted_before_handler(self):
        out = dirtyjson.migrate('{"a": NaN, "a": 2}',
                                on_nonfinite=0, on_duplicate='last')
        self.assertEqual(json.loads(out), {"a": 2})

    def test_combined_handlers(self):
        source = '{"t": NaN, "t": Infinity, "ok": [1, {"z": -Infinity}]}'
        out = dirtyjson.migrate(source, on_nonfinite="n/a",
                                on_duplicate='first')
        self.assertEqual(json.loads(out),
                         {"t": "n/a", "ok": [1, {"z": "n/a"}]})

    def test_source_text_is_not_modified(self):
        source = '{"a": NaN, "a": 1}'
        dirtyjson.migrate(source, on_nonfinite=0, on_duplicate='last')
        self.assertEqual(source, '{"a": NaN, "a": 1}')

    def test_issue_equality(self):
        self.assertEqual(MigrationIssue('nan', '$["a"]'),
                         MigrationIssue('nan', '$["a"]'))
        self.assertNotEqual(MigrationIssue('nan', '$["a"]'),
                            MigrationIssue('nan', '$["b"]'))
