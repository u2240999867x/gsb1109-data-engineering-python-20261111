import unittest
from decimal import Decimal

import agate
from agate import EXACT, INVALID, LOSSY, TypeChecker
from agate.data_types import Boolean, Number, Text
from agate.type_checker import TypeCheckIssue


class TestTypeChecker(unittest.TestCase):
    def test_exact_values(self):
        result = TypeChecker(Number()).run(['1', '2.7', None, ''])

        self.assertEqual(result.values, ('1', '2.7', None, ''))
        self.assertEqual(len(result.report), 4)
        self.assertEqual([issue.status for issue in result.report], [EXACT, EXACT, EXACT, EXACT])
        self.assertEqual(len(result.exact), 4)
        self.assertEqual(result.lossy, ())
        self.assertEqual(result.invalid, ())
        self.assertTrue(result.valid)
        self.assertTrue(result.exact_match)

    def test_null_values_are_exact(self):
        result = TypeChecker(Number()).run(['N/A', 'none'])

        self.assertEqual([issue.status for issue in result.report], [EXACT, EXACT])
        self.assertEqual([issue.casted_value for issue in result.report], [None, None])

    def test_lossy_leading_zeroes(self):
        result = TypeChecker(Number()).run(['007', '0010'])

        self.assertEqual(result.valid, True)
        self.assertEqual(result.exact_match, False)
        self.assertEqual(len(result.lossy), 2)

        issue = result.lossy[0]
        self.assertIsInstance(issue, TypeCheckIssue)
        self.assertEqual(issue.status, LOSSY)
        self.assertEqual(issue.row, 0)
        self.assertEqual(issue.value, '007')
        self.assertEqual(issue.casted_value, Decimal('7'))

        self.assertEqual(result.lossy[1].row, 1)
        self.assertEqual(result.lossy[1].value, '0010')
        self.assertEqual(result.lossy[1].casted_value, Decimal('10'))

    def test_lossy_normalization(self):
        result = TypeChecker(Number()).run(['1,000', '50%'])

        self.assertEqual([issue.status for issue in result.report], [LOSSY, LOSSY])
        self.assertEqual([issue.casted_value for issue in result.report], [Decimal('1000'), Decimal('50')])

    def test_invalid_values(self):
        result = TypeChecker(Number()).run(['1', 'quack'])

        self.assertEqual([issue.status for issue in result.report], [EXACT, INVALID])
        self.assertFalse(result.valid)
        self.assertFalse(result.exact_match)

        issue = result.invalid[0]
        self.assertEqual(issue.row, 1)
        self.assertEqual(issue.value, 'quack')
        self.assertIsNone(issue.casted_value)

    def test_report_keeps_row_position_and_input(self):
        result = TypeChecker(Number()).run(['007', '1', 'nope'])

        statuses = [(issue.row, issue.value, issue.status) for issue in result.report]
        self.assertEqual(statuses, [
            (0, '007', LOSSY),
            (1, '1', EXACT),
            (2, 'nope', INVALID)
        ])
        self.assertEqual(result.issue_at(2).status, INVALID)
        self.assertEqual(result.issue_at(1).value, '1')

    def test_boolean_representation_changes(self):
        result = TypeChecker(Boolean()).run(['1', '0', 'yes'])

        self.assertEqual([issue.status for issue in result.report], [LOSSY, LOSSY, LOSSY])
        self.assertEqual([issue.casted_value for issue in result.report], [True, False, True])

    def test_text_is_exact(self):
        result = TypeChecker(Text()).run(['007', 'a'])

        self.assertEqual([issue.status for issue in result.report], [EXACT, EXACT])
        self.assertTrue(result.exact_match)
        self.assertEqual([issue.casted_value for issue in result.report], ['007', 'a'])

    def test_iterator_is_materialized(self):
        def generate():
            yield '007'
            yield '1'

        result = TypeChecker(Number()).run(generate())

        self.assertEqual(result.values, ('007', '1'))
        self.assertEqual(result.report[0].status, LOSSY)
        self.assertEqual(result.report[1].status, EXACT)

        table = agate.Table([(value,) for value in result.values], ['id'], column_types=[Text()])
        self.assertEqual(table.rows[0]['id'], '007')

    def test_empty_input(self):
        result = TypeChecker(Number()).run(())

        self.assertEqual(result.values, ())
        self.assertEqual(result.report, ())
        self.assertTrue(result.valid)
        self.assertTrue(result.exact_match)

    def test_invalid_data_type(self):
        with self.assertRaises(ValueError):
            TypeChecker('Number')
