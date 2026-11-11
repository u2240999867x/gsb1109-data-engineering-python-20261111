from unittest import TestCase

import dirtyjson
from dirtyjson.repair import (Edit, apply_edits, suggest_repairs,
                              STRUCTURAL_PUNCTUATION)


class TestRepair(TestCase):
    def test_missing_comma_between_object_entries(self):
        content = '{"a": 1 "b": 2}'
        result = suggest_repairs(content)
        self.assertTrue(result.complete)
        self.assertFalse(result.already_valid)
        self.assertTrue(result.candidates)
        for candidate in result.candidates:
            self.assertEqual(candidate.distance, 1)
            edit = candidate.edits[0]
            self.assertEqual(edit.action, 'insert')
            self.assertEqual(edit.char, ',')
            self.assertEqual(candidate.text, apply_edits(content, candidate.edits))
            self.assertEqual(candidate.value, {'a': 1, 'b': 2})
        # Both sides of the intervening whitespace are equally valid.
        positions = sorted(c.edits[0].pos for c in result.candidates)
        self.assertEqual(positions, [7, 8])

    def test_missing_colon(self):
        content = '{"a" 1}'
        result = suggest_repairs(content)
        self.assertTrue(result.complete)
        self.assertTrue(result.candidates)
        candidate = result.candidates[0]
        self.assertEqual(candidate.distance, 1)
        self.assertEqual(candidate.edits[0], Edit('insert', ':', 5))
        self.assertEqual(candidate.value, {'a': 1})

    def test_missing_closing_brace(self):
        content = '{"a": [1, 2}'
        result = suggest_repairs(content)
        self.assertTrue(result.complete)
        self.assertTrue(result.candidates)
        for candidate in result.candidates:
            self.assertEqual(candidate.value, {'a': [1, 2]})
            self.assertEqual(candidate.text,
                             apply_edits(content, candidate.edits))

    def test_missing_comma_in_array(self):
        content = '[1 2]'
        result = suggest_repairs(content)
        self.assertTrue(result.complete)
        self.assertTrue(result.candidates)
        self.assertEqual(result.candidates[0].value, [1, 2])

    def test_stray_punctuation_is_deleted(self):
        content = '{"a":: 1}'
        result = suggest_repairs(content)
        self.assertTrue(result.complete)
        self.assertTrue(result.candidates)
        deletions = [c for c in result.candidates
                     if c.edits[0].action == 'delete']
        self.assertTrue(deletions)
        for candidate in deletions:
            self.assertEqual(candidate.edits[0].char, ':')
            self.assertEqual(candidate.value, {'a': 1})

    def test_string_contents_and_keys_are_untouched(self):
        content = '{"ke,y": "va:l" "x": 1}'
        result = suggest_repairs(content)
        self.assertTrue(result.candidates)
        for candidate in result.candidates:
            self.assertEqual(candidate.value, {'ke,y': 'va:l', 'x': 1})
            # Only structural punctuation may differ from the original.
            for edit in candidate.edits:
                self.assertIn(edit.char, STRUCTURAL_PUNCTUATION)

    def test_multiple_missing_marks_need_multiple_edits(self):
        content = '{"a": 1 "b": [2 3]}'
        result = suggest_repairs(content)
        self.assertTrue(result.complete)
        self.assertTrue(result.candidates)
        distances = set(c.distance for c in result.candidates)
        self.assertEqual(distances, {2})
        for candidate in result.candidates:
            self.assertEqual(candidate.value, {'a': 1, 'b': [2, 3]})
            self.assertEqual(candidate.text,
                             apply_edits(content, candidate.edits))

    def test_already_valid_document(self):
        result = suggest_repairs('{"a": 1, "b": [2, 3]}')
        self.assertTrue(result.complete)
        self.assertTrue(result.already_valid)
        self.assertEqual(result.candidates, [])

    def test_lenient_features_still_accepted(self):
        # Trailing commas, single quotes, unquoted keys and comments are
        # part of the normal loads acceptance range and need no repair.
        content = "/* c */ {a: 'x', b: [1, 2,],}"
        result = suggest_repairs(content)
        self.assertTrue(result.already_valid)

    def test_budget_exhaustion_reports_incomplete(self):
        content = '{"a": 1 "b": 2}'
        result = suggest_repairs(content, budget=1)
        self.assertFalse(result.complete)
        self.assertEqual(result.candidates, [])

    def test_no_repair_within_max_edits(self):
        result = suggest_repairs('{"a" 1}', max_edits=0)
        self.assertTrue(result.complete)
        self.assertEqual(result.candidates, [])

    def test_trailing_garbage_is_not_a_valid_repair(self):
        # A candidate only counts if the whole document is consumed.
        result = suggest_repairs('[1 2] 3')
        for candidate in result.candidates:
            self.assertEqual(dirtyjson.loads(candidate.text),
                             candidate.value)

    def test_edit_describe(self):
        content = '{"a" 1}'
        edit = Edit('insert', ':', 5)
        self.assertIn('line 1 column 6', edit.describe(content))
        self.assertIn("':'", edit.describe(content))
