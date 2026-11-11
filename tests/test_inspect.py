from tests import unittest, OrderedDict

import jmespath
import jmespath.functions
from jmespath import MISSING
from jmespath.visitor import Options


class TestInspectMode(unittest.TestCase):
    def inspect(self, expression, data):
        return jmespath.inspect(expression, data)

    def test_missing_field_is_not_null(self):
        result = self.inspect('{missing: missing, present: present}',
                              {'present': None})
        self.assertIs(result['missing'], MISSING)
        self.assertIsNone(result['present'])

    def test_top_level_missing_field(self):
        self.assertIs(self.inspect('nope', {'a': 1}), MISSING)
        self.assertIsNone(self.inspect('a', {'a': None}))

    def test_missing_is_singleton(self):
        self.assertIs(self.inspect('a', {}), self.inspect('b', {}))
        self.assertIs(self.inspect('a', {}), MISSING)

    def test_missing_propagates_through_subexpression(self):
        self.assertIs(self.inspect('a.b.c', {}), MISSING)
        self.assertIs(self.inspect('a.b.c', {'a': {}}), MISSING)
        self.assertIsNone(self.inspect('a.b.c', {'a': {'b': None}}))
        self.assertEqual(self.inspect('a.b.c', {'a': {'b': {'c': 1}}}), 1)

    def test_missing_propagates_through_index(self):
        self.assertIs(self.inspect('a[0]', {}), MISSING)
        self.assertIs(self.inspect('a[0].b', {}), MISSING)

    def test_field_on_non_object_still_null(self):
        # Type mismatches are still null, only absent keys are missing.
        self.assertIsNone(self.inspect('a.b', {'a': [1, 2]}))
        self.assertIsNone(self.inspect('a.b', {'a': 'str'}))

    def test_projection_drops_missing_like_null(self):
        data = [{'a': 1}, {}, {'a': None}, {'a': 2}]
        self.assertEqual(self.inspect('[*].a', data), [1, 2])

    def test_value_projection_drops_missing_like_null(self):
        data = {'x': {'a': 1}, 'y': {}, 'z': {'a': None}}
        self.assertEqual(self.inspect('*.a', data), [1])

    def test_filter_projection_drops_missing_like_null(self):
        data = [{'keep': True}, {'keep': True, 'a': None},
                {'keep': True, 'a': 5}]
        self.assertEqual(self.inspect('[?keep].a', data), [5])

    def test_missing_is_falsy_in_filter(self):
        # Note: 0 is truthy in JMESPath, missing/null are not.
        data = [{'a': 1}, {}, {'a': None}, {'a': 0}]
        self.assertEqual(self.inspect('[?a]', data), [{'a': 1}, {'a': 0}])

    def test_filter_can_select_missing(self):
        data = [{'a': 1}, {}, {'a': None}]
        self.assertEqual(self.inspect('[?is_missing(a)]', data), [{}])

    def test_missing_is_falsy_in_boolean_expressions(self):
        self.assertEqual(self.inspect('a || `"fallback"`', {}), 'fallback')
        # && short-circuits and returns the falsy missing value itself.
        self.assertIs(self.inspect('a && a.b', {}), MISSING)
        self.assertTrue(self.inspect('!a', {}))

    def test_missing_not_equal_to_null(self):
        self.assertFalse(self.inspect('a == `null`', {}))
        self.assertTrue(self.inspect('a != `null`', {}))
        self.assertTrue(self.inspect('a == `null`', {'a': None}))
        self.assertTrue(self.inspect('a == b', {}))

    def test_ordering_comparator_with_missing(self):
        self.assertIsNone(self.inspect('a < `1`', {}))

    def test_multi_select_list_keeps_missing(self):
        result = self.inspect('[a, b]', {'a': None})
        self.assertIsNone(result[0])
        self.assertIs(result[1], MISSING)

    def test_multi_select_on_missing_base(self):
        self.assertIs(self.inspect('a.{b: b}', {}), MISSING)
        self.assertIsNone(self.inspect('a.{b: b}', {'a': None}))

    def test_is_missing_function(self):
        self.assertTrue(self.inspect('is_missing(a)', {}))
        self.assertFalse(self.inspect('is_missing(a)', {'a': None}))
        self.assertFalse(self.inspect('is_missing(@)', {}))

    def test_type_function_distinguishes_missing(self):
        self.assertEqual(self.inspect('type(a)', {}), 'missing')
        self.assertEqual(self.inspect('type(a)', {'a': None}), 'null')

    def test_not_null_coalesces_missing(self):
        self.assertEqual(
            self.inspect('not_null(a, b, `"fallback"`)', {'b': None}),
            'fallback')
        self.assertEqual(
            self.inspect('not_null(a, b)', {'b': 'present'}), 'present')

    def test_to_number_of_missing_is_null(self):
        self.assertIsNone(self.inspect('to_number(a)', {}))

    def test_function_composition(self):
        # MISSING flows through function composition as a real value.
        result = self.inspect('{x: a} | values(@)', {})
        self.assertEqual(len(result), 1)
        self.assertIs(result[0], MISSING)
        wrapped = self.inspect('to_array(a)', {})
        self.assertEqual(len(wrapped), 1)
        self.assertIs(wrapped[0], MISSING)

    def test_typed_function_rejects_missing(self):
        with self.assertRaises(jmespath.exceptions.JMESPathTypeError):
            self.inspect('length(a)', {})

    def test_missing_repr_and_falsy(self):
        self.assertEqual(repr(MISSING), 'MISSING')
        self.assertFalse(bool(MISSING))

    def test_inspect_keeps_user_options(self):
        result = jmespath.inspect(
            '{a: a, b: b}.*', {'b': 'b', 'a': 'a'},
            options=Options(dict_cls=OrderedDict))
        self.assertEqual(result, ['a', 'b'])

    def test_inspect_does_not_mutate_passed_options(self):
        options = Options()
        jmespath.inspect('a', {}, options=options)
        self.assertFalse(options.preserve_missing)


class TestPreserveMissingOption(unittest.TestCase):
    def test_search_with_preserve_missing(self):
        options = Options(preserve_missing=True)
        self.assertIs(jmespath.search('a', {}, options=options), MISSING)
        self.assertIsNone(jmespath.search('a', {'a': None},
                                          options=options))

    def test_compiled_expression_with_preserve_missing(self):
        compiled = jmespath.compile('a.b')
        options = Options(preserve_missing=True)
        self.assertIs(compiled.search({}, options=options), MISSING)


class TestSearchUnchanged(unittest.TestCase):
    def test_search_still_returns_null_for_missing(self):
        self.assertIsNone(jmespath.search('nope', {}))
        self.assertIsNone(jmespath.search('a.b.c', {}))

    def test_search_multiselect_shape_unchanged(self):
        self.assertEqual(
            jmespath.search('{missing: missing, present: present}',
                            {'present': None}),
            {'missing': None, 'present': None})

    def test_search_projection_shape_unchanged(self):
        data = [{'a': 1}, {}, {'a': None}, {'a': 2}]
        self.assertEqual(jmespath.search('[*].a', data), [1, 2])

    def test_is_missing_always_false_in_search(self):
        self.assertFalse(jmespath.search('is_missing(a)', {}))
        self.assertEqual(jmespath.search('type(a)', {}), 'null')

    def test_missing_sentinel_never_in_search_result(self):
        data = {'a': {'b': None}, 'c': [{}]}
        result = jmespath.search('{x: a.b, y: c[0].z, z: nope}', data)
        self.assertEqual(result, {'x': None, 'y': None, 'z': None})


if __name__ == '__main__':
    unittest.main()
