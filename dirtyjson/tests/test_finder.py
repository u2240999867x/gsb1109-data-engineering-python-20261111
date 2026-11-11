from __future__ import absolute_import
from unittest import TestCase

import dirtyjson
from dirtyjson import find_candidates


class TestFindCandidates(TestCase):
    def test_payload_surrounded_by_noise(self):
        s = ('INFO 2026-11-11 request ok\n'
             'response: {"a": 1, "b": [2, 3]}\n'
             'console.log("done");\n')
        start = s.index('{')
        end = s.index('}') + 1
        candidates = find_candidates(s)
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.start, start)
        self.assertEqual(candidate.end, end)
        self.assertTrue(candidate.boundary_confident)
        self.assertEqual(candidate.value, {'a': 1, 'b': [2, 3]})
        self.assertEqual(s[candidate.start:candidate.end],
                         '{"a": 1, "b": [2, 3]}')

    def test_braces_inside_preceding_strings_are_not_candidates(self):
        s = ('diag: "use {x} here" and \'a {y} there\'\n'
             'payload: {"real": true}')
        candidates = find_candidates(s)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].value, {'real': True})
        self.assertEqual(s[candidates[0].start:candidates[0].end],
                         '{"real": true}')

    def test_escaped_quote_in_preceding_string(self):
        s = 'note: "he said \\"{not json}\\" ok" {"real": 1}'
        candidates = find_candidates(s)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].value, {'real': 1})

    def test_braces_inside_comments_are_not_candidates(self):
        s = ('// logged {"debug": 1} earlier\n'
             '/* block { "comment": [1] } */\n'
             '{"real": 2}')
        candidates = find_candidates(s)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].value, {'real': 2})

    def test_multiple_parseable_segments_all_reported(self):
        s = ('first: {"a": 1}\n'
             'noise in between\n'
             'second: [1, 2, 3]\n')
        candidates = find_candidates(s)
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0].value, {'a': 1})
        self.assertEqual(candidates[1].value, [1, 2, 3])
        self.assertEqual(s[candidates[0].start:candidates[0].end], '{"a": 1}')
        self.assertEqual(s[candidates[1].start:candidates[1].end], '[1, 2, 3]')

    def test_script_block_braces_are_not_candidates(self):
        s = 'if (x) { return; } {"real": 1}'
        candidates = find_candidates(s)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].value, {'real': 1})

    def test_lenient_parsing_inside_candidate(self):
        s = ("payload: {unquoted: 'single', hex: 0x80, list: [1, 2,],"
             " // a comment\n"
             " nan: NaN}\n")
        candidates = find_candidates(s)
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertTrue(candidate.boundary_confident)
        self.assertEqual(candidate.value['unquoted'], 'single')
        self.assertEqual(candidate.value['hex'], 0x80)
        self.assertEqual(candidate.value['list'], [1, 2])
        self.assertTrue(candidate.value['nan'] != candidate.value['nan'])

    def test_nested_objects_not_reported_separately(self):
        s = '{"outer": {"inner": [1, {"deep": 2}]}} trailing'
        candidates = find_candidates(s)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].value,
                         {'outer': {'inner': [1, {'deep': 2}]}})
        self.assertEqual(candidates[0].end, s.index(' trailing'))

    def test_truncated_structure_boundary_not_confident(self):
        s = 'prefix {"a": 1, "b": [2, 3'
        candidates = find_candidates(s)
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertFalse(candidate.boundary_confident)
        self.assertIsNone(candidate.value)
        self.assertEqual(candidate.start, s.index('{'))
        self.assertEqual(candidate.end, len(s))
        self.assertIsNotNone(candidate.error)

    def test_unterminated_string_boundary_not_confident(self):
        s = 'prefix {"a": "cut off'
        candidates = find_candidates(s)
        self.assertEqual(len(candidates), 1)
        self.assertFalse(candidates[0].boundary_confident)
        self.assertIsNone(candidates[0].value)

    def test_unterminated_comment_at_end_of_candidate(self):
        s = '{"a": 1} // trailing note with no newline'
        candidates = find_candidates(s)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].value, {'a': 1})
        self.assertTrue(candidates[0].boundary_confident)

    def test_no_candidates(self):
        self.assertEqual(find_candidates('no json here at all'), [])
        self.assertEqual(find_candidates(''), [])

    def test_candidate_span_round_trips_through_loads(self):
        s = 'junk {"a": [1, 2], "b": {c: 3,}} more junk [4, 5] tail'
        candidates = find_candidates(s)
        self.assertEqual(len(candidates), 2)
        for candidate in candidates:
            self.assertTrue(candidate.boundary_confident)
            span = s[candidate.start:candidate.end]
            self.assertEqual(dirtyjson.loads(span), candidate.value)
