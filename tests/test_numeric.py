import json
from decimal import Decimal

from tests import unittest

import jmespath
from jmespath import exceptions
from jmespath import numeric


def load_with_decimal(document):
    # Simulates an upstream parser that keeps monetary amounts as
    # Decimal instead of binary float.
    return json.loads(document, parse_float=Decimal)


class TestDecimalRecognizedAsNumber(unittest.TestCase):
    def test_type_reports_number(self):
        self.assertEqual(jmespath.search('type(@)', Decimal('1.5')),
                         'number')

    def test_abs_preserves_decimal(self):
        result = jmespath.search('abs(@)', Decimal('-1.5'))
        self.assertEqual(result, Decimal('1.5'))
        self.assertIsInstance(result, Decimal)

    def test_ceil_and_floor_return_integer(self):
        self.assertEqual(jmespath.search('ceil(@)', Decimal('1.2')), 2)
        self.assertEqual(jmespath.search('floor(@)', Decimal('-1.2')), -2)

    def test_to_number_keeps_decimal_precision(self):
        result = jmespath.search('to_number(@)', Decimal('1.1'))
        self.assertEqual(result, Decimal('1.1'))
        self.assertIsInstance(result, Decimal)

    def test_to_string_renders_decimal_as_json_number(self):
        self.assertEqual(
            jmespath.search('to_string(@)', Decimal('1.5')), '1.5')
        # Scale is preserved rather than normalised through float.
        self.assertEqual(
            jmespath.search('to_string(@)', Decimal('1.50')), '1.50')

    def test_to_string_handles_nested_decimals(self):
        self.assertEqual(
            jmespath.search(
                'to_string(@)', [Decimal('1.5'), Decimal('2.20')]),
            '[1.5,2.20]')
        self.assertEqual(
            jmespath.search(
                'to_string(@)', {'amount': Decimal('1.5')}),
            '{"amount":1.5}')

    def test_to_string_decimal_matches_float_rendering(self):
        self.assertEqual(
            jmespath.search('to_string(@)', Decimal('1.5')),
            jmespath.search('to_string(@)', 1.5))

    def test_join_of_decimal_to_string_has_no_embedded_quotes(self):
        result = jmespath.search(
            'join(`"|"`, [*].to_string(@))',
            [Decimal('1.5'), Decimal('2.5')])
        self.assertEqual(result, '1.5|2.5')

    def test_decimal_survives_filter_comparison_with_integer(self):
        result = jmespath.search(
            '[?a >= `1`].a', [{'a': Decimal('3')}])
        self.assertEqual(result, [Decimal('3')])


class TestDecimalPrecisionInAggregations(unittest.TestCase):
    def test_sum_preserves_decimal(self):
        result = jmespath.search(
            'sum(@)', [Decimal('1.1'), Decimal('2.2')])
        self.assertEqual(result, Decimal('3.3'))
        self.assertIsInstance(result, Decimal)

    def test_avg_preserves_decimal(self):
        result = jmespath.search(
            'avg(@)', [Decimal('1.1'), Decimal('2.2')])
        self.assertEqual(result, Decimal('1.65'))
        self.assertIsInstance(result, Decimal)

    def test_avg_does_not_divide_by_binary_float(self):
        result = jmespath.search('avg(@)', [Decimal('1'), Decimal('2')])
        self.assertEqual(result, Decimal('1.5'))
        self.assertIsInstance(result, Decimal)

    def test_min_max_preserve_decimal(self):
        values = [Decimal('2.2'), Decimal('1.1')]
        self.assertEqual(jmespath.search('min(@)', values),
                         Decimal('1.1'))
        self.assertEqual(jmespath.search('max(@)', values),
                         Decimal('2.2'))

    def test_sort_orders_decimals_without_coercion(self):
        result = jmespath.search(
            'sort(@)', [Decimal('2.2'), Decimal('1.1')])
        self.assertEqual(result, [Decimal('1.1'), Decimal('2.2')])
        self.assertIsInstance(result[0], Decimal)

    def test_sort_by_min_by_max_by_preserve_decimal_keys(self):
        data = [{'x': Decimal('2.2')}, {'x': Decimal('1.1')}]
        self.assertEqual(
            jmespath.search('sort_by(@, &x)', data),
            [{'x': Decimal('1.1')}, {'x': Decimal('2.2')}])
        self.assertEqual(
            jmespath.search('min_by(@, &x)', data), {'x': Decimal('1.1')})
        self.assertEqual(
            jmespath.search('max_by(@, &x)', data), {'x': Decimal('2.2')})

    def test_decimal_input_from_json_parser_keeps_precision(self):
        data = load_with_decimal('{"amounts": [0.1, 0.2]}')
        result = jmespath.search('sum(amounts)', data)
        self.assertEqual(result, Decimal('0.3'))
        self.assertIsInstance(result, Decimal)


class TestMixedIntegerAndDecimal(unittest.TestCase):
    def test_sum_int_and_decimal_stays_decimal(self):
        result = jmespath.search('sum(@)', [1, Decimal('2.2')])
        self.assertEqual(result, Decimal('3.2'))
        self.assertIsInstance(result, Decimal)

    def test_avg_int_and_decimal_stays_decimal(self):
        result = jmespath.search('avg(@)', [2, Decimal('2.2')])
        self.assertEqual(result, Decimal('2.1'))
        self.assertIsInstance(result, Decimal)

    def test_sort_int_and_decimal(self):
        result = jmespath.search('sort(@)', [2, Decimal('1.1'), 3])
        self.assertEqual(result, [Decimal('1.1'), 2, 3])

    def test_ordering_comparator_int_and_decimal(self):
        self.assertTrue(
            jmespath.search('a < b', {'a': Decimal('2'), 'b': 3}))
        self.assertTrue(
            jmespath.search('a > b', {'a': 3, 'b': Decimal('2')}))


class TestMixedFloatAndDecimalRejected(unittest.TestCase):
    def _assert_numeric_error(self, expression, data, function_name,
                              argument_index):
        with self.assertRaises(exceptions.JMESPathNumericTypeError) as cm:
            jmespath.search(expression, data)
        error = cm.exception
        message = str(error)
        self.assertIn(function_name, message)
        self.assertIn('argument %s' % argument_index, message)
        self.assertIn('Decimal', message)
        self.assertIn('float', message)
        # The specialized error must still be catchable as the plain
        # type error so existing handlers keep working.
        self.assertIsInstance(error, exceptions.JMESPathTypeError)

    def test_sum_rejects_float_and_decimal_in_both_orders(self):
        self._assert_numeric_error(
            'sum(@)', [1.1, Decimal('2.2')], 'sum()', 1)
        self._assert_numeric_error(
            'sum(@)', [Decimal('2.2'), 1.1], 'sum()', 1)

    def test_avg_rejects_float_and_decimal(self):
        self._assert_numeric_error(
            'avg(@)', [1.1, Decimal('2.2')], 'avg()', 1)

    def test_min_max_reject_float_and_decimal(self):
        self._assert_numeric_error(
            'min(@)', [1.1, Decimal('2.2')], 'min()', 1)
        self._assert_numeric_error(
            'max(@)', [1.1, Decimal('2.2')], 'max()', 1)

    def test_sort_rejects_float_and_decimal(self):
        self._assert_numeric_error(
            'sort(@)', [1.1, Decimal('2.2')], 'sort()', 1)

    def test_sort_by_min_by_max_by_reject_mixed_keys(self):
        data = [{'x': Decimal('2.2')}, {'x': 1.1}]
        self._assert_numeric_error(
            'sort_by(@, &x)', data, 'sort_by()', 2)
        self._assert_numeric_error(
            'min_by(@, &x)', data, 'min_by()', 2)
        self._assert_numeric_error(
            'max_by(@, &x)', data, 'max_by()', 2)

    def test_ordering_comparator_rejects_float_and_decimal(self):
        with self.assertRaises(exceptions.JMESPathNumericTypeError) as cm:
            jmespath.search('a > b', {'a': Decimal('2'), 'b': 1.5})
        message = str(cm.exception)
        self.assertIn('ordering operator "gt (>)"', message)
        self.assertIn('operand 1', message)

        with self.assertRaises(exceptions.JMESPathNumericTypeError) as cm:
            jmespath.search('a < b', {'a': 1.5, 'b': Decimal('2')})
        message = str(cm.exception)
        self.assertIn('ordering operator "lt (<)"', message)
        self.assertIn('operand 2', message)

    def test_filter_projection_surfaces_operator_context(self):
        with self.assertRaises(exceptions.JMESPathNumericTypeError) as cm:
            jmespath.search(
                '[?a > `1.5`].a', [{'a': Decimal('2')}])
        self.assertIn('ordering operator "gt (>)"', str(cm.exception))


class TestEqualityNeverCoerces(unittest.TestCase):
    def test_decimal_equality_does_not_raise(self):
        # Equality performs no arithmetic, so it never triggers the
        # mixing rule and never converts either operand.
        self.assertFalse(
            jmespath.search('a == b', {'a': Decimal('1.1'), 'b': 1.1}))
        self.assertTrue(
            jmespath.search('a != b', {'a': Decimal('1.1'), 'b': 1.1}))
        self.assertTrue(
            jmespath.search('a == b',
                            {'a': Decimal('1.1'), 'b': Decimal('1.1')}))


class TestPlainJsonBehaviorUnchanged(unittest.TestCase):
    def test_float_sum_stays_float(self):
        result = jmespath.search('sum(@)', [1.1, 2.2])
        self.assertEqual(result, 3.3000000000000003)
        self.assertIsInstance(result, float)

    def test_int_and_float_mix_still_works(self):
        self.assertEqual(jmespath.search('sum(@)', [1, 2.5]), 3.5)
        self.assertEqual(
            jmespath.search('sort(@)', [3, 1.5, 2]), [1.5, 2, 3])

    def test_float_avg_unchanged(self):
        self.assertEqual(jmespath.search('avg(@)', [1, 2]), 1.5)

    def test_float_ordering_unchanged(self):
        self.assertTrue(
            jmespath.search('a > b', {'a': 1.5, 'b': 1.4}))

    def test_boolean_is_still_not_a_number(self):
        with self.assertRaises(exceptions.JMESPathTypeError):
            jmespath.search('sum(@)', [1, True])
        self.assertIsNone(numeric.numeric_kind(True))
        self.assertEqual(
            jmespath.search('type(@)', True), 'boolean')


class TestByFunctionTypeErrorsKeepFunctionName(unittest.TestCase):
    def test_min_by_non_numeric_key_names_function(self):
        with self.assertRaises(exceptions.JMESPathTypeError) as cm:
            jmespath.search(
                'min_by(@, &x)', [{'x': 1}, {'x': 'a'}])
        self.assertIn('min_by()', str(cm.exception))

    def test_max_by_boolean_key_names_function(self):
        with self.assertRaises(exceptions.JMESPathTypeError) as cm:
            jmespath.search('max_by(@, &x)', [{'x': True}])
        self.assertIn('max_by()', str(cm.exception))
        self.assertIn('boolean', str(cm.exception))

    def test_string_keys_remain_supported(self):
        data = [{'x': 'b'}, {'x': 'a'}]
        self.assertEqual(
            jmespath.search('sort_by(@, &x)', data),
            [{'x': 'a'}, {'x': 'b'}])
        self.assertEqual(
            jmespath.search('max_by(@, &x)', data), {'x': 'b'})


class TestNumericPolicyHelpers(unittest.TestCase):
    def test_numeric_kind_classification(self):
        self.assertEqual(numeric.numeric_kind(Decimal('1')),
                         numeric.DECIMAL_KIND)
        self.assertEqual(numeric.numeric_kind(1),
                         numeric.JSON_NUMBER_KIND)
        self.assertEqual(numeric.numeric_kind(1.5),
                         numeric.JSON_NUMBER_KIND)
        self.assertIsNone(numeric.numeric_kind('1'))
        self.assertIsNone(numeric.numeric_kind(None))

    def test_check_numeric_values_accepts_homogeneous_kinds(self):
        numeric.check_numeric_values('sum', 1, [1, 2.5])
        numeric.check_numeric_values(
            'sum', 1, [Decimal('1'), 2, Decimal('3')])
        numeric.check_numeric_values('sum', 1, [])

    def test_check_numeric_values_rejects_mix(self):
        with self.assertRaises(exceptions.JMESPathNumericTypeError) as cm:
            numeric.check_numeric_values(
                'sum', 1, [Decimal('1'), 1.5])
        self.assertIn('sum()', str(cm.exception))
