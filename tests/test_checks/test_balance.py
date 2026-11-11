from collections import OrderedDict
from decimal import Decimal

import agate
from agate import Table, TableSet
from agate.checks import Balance, CheckFailure, CheckResult
from agate.data_types import Number, Text
from agate.exceptions import DataTypeError
from agate.testcase import AgateTestCase


class TestBalance(AgateTestCase):
    def setUp(self):
        self.number_type = Number()
        self.text_type = Text()

        self.orders = Table((
            ('CN', 'CNY', Decimal('100.00')),
            ('CN', 'CNY', Decimal('50.00')),
            ('US', 'USD', Decimal('200.00')),
            ('US', 'USD', Decimal('300.00')),
        ), ['region', 'currency', 'sales'], [self.text_type, self.text_type, self.number_type])

        self.refunds = Table((
            ('CN', 'CNY', Decimal('10.00')),
            ('US', 'USD', Decimal('20.00')),
        ), ['region', 'currency', 'refund'], [self.text_type, self.text_type, self.number_type])

        self.settlements = Table((
            ('CN', 'CNY', Decimal('140.00')),
            ('US', 'USD', Decimal('480.00')),
        ), ['region', 'currency', 'settled'], [self.text_type, self.text_type, self.number_type])

        self.tables = [self.orders, self.refunds, self.settlements]

        self.check = Balance(
            left_metrics=[('sales', (0, 'sales'))],
            right_metrics=[('refund', (1, 'refund')), ('settled', (2, 'settled'))],
            tolerance=Decimal('0'),
            name='sales minus refunds equals settlement',
        )

    def test_balanced_groups_pass(self):
        result = self.check.run(self.tables, group_by=['region', 'currency'])

        self.assertIsInstance(result, CheckResult)
        self.assertTrue(result.passed)
        self.assertTrue(bool(result))
        self.assertEqual(result.failures, ())

    def test_failing_group_reports_values_and_difference(self):
        settlements = Table((
            ('CN', 'CNY', Decimal('140.00')),
            ('US', 'USD', Decimal('470.00')),
        ), ['region', 'currency', 'settled'], [self.text_type, self.text_type, self.number_type])

        result = self.check.run([self.orders, self.refunds, settlements], group_by=['region', 'currency'])

        self.assertFalse(result.passed)
        self.assertFalse(bool(result))
        self.assertEqual(len(result.failures), 1)

        failure = result.failures[0]

        self.assertIsInstance(failure, CheckFailure)
        self.assertEqual(failure.group_key, ('US', 'USD'))
        self.assertEqual(failure.values, OrderedDict([
            ('sales', Decimal('500.00')),
            ('refund', Decimal('20.00')),
            ('settled', Decimal('470.00')),
        ]))
        self.assertEqual(failure.difference, Decimal('10.00'))

    def test_difference_is_signed(self):
        settlements = Table((
            ('CN', 'CNY', Decimal('140.00')),
            ('US', 'USD', Decimal('490.00')),
        ), ['region', 'currency', 'settled'], [self.text_type, self.text_type, self.number_type])

        result = self.check.run([self.orders, self.refunds, settlements], group_by=['region', 'currency'])

        self.assertEqual(result.failures[0].group_key, ('US', 'USD'))
        self.assertEqual(result.failures[0].difference, Decimal('-10.00'))

    def test_tolerance_absorbs_small_difference(self):
        settlements = Table((
            ('CN', 'CNY', Decimal('140.00')),
            ('US', 'USD', Decimal('470.00')),
        ), ['region', 'currency', 'settled'], [self.text_type, self.text_type, self.number_type])

        check = Balance(
            left_metrics=[('sales', (0, 'sales'))],
            right_metrics=[('refund', (1, 'refund')), ('settled', (2, 'settled'))],
            tolerance=Decimal('10.00'),
        )

        self.assertTrue(check.run([self.orders, self.refunds, settlements],
                                  group_by=['region', 'currency']).passed)

        strict = Balance(
            left_metrics=[('sales', (0, 'sales'))],
            right_metrics=[('refund', (1, 'refund')), ('settled', (2, 'settled'))],
            tolerance=Decimal('9.99'),
        )

        self.assertFalse(strict.run([self.orders, self.refunds, settlements],
                                    group_by=['region', 'currency']).passed)

    def test_negative_tolerance_rejected(self):
        with self.assertRaises(ValueError):
            Balance(
                left_metrics=[('sales', (0, 'sales'))],
                right_metrics=[('settled', (2, 'settled'))],
                tolerance=Decimal('-0.01'),
            )

    def test_missing_group_is_not_treated_as_zero(self):
        refunds = Table((
            ('CN', 'CNY', Decimal('10.00')),
        ), ['region', 'currency', 'refund'], [self.text_type, self.text_type, self.number_type])

        settlements = Table((
            ('CN', 'CNY', Decimal('140.00')),
        ), ['region', 'currency', 'settled'], [self.text_type, self.text_type, self.number_type])

        result = self.check.run([self.orders, refunds, settlements], group_by=['region', 'currency'])

        self.assertEqual(len(result.failures), 1)

        failure = result.failures[0]
        self.assertEqual(failure.group_key, ('US', 'USD'))
        self.assertIsNone(failure.values['refund'])
        self.assertIsNone(failure.values['settled'])
        self.assertEqual(failure.values['sales'], Decimal('500.00'))
        self.assertIsNone(failure.difference)

    def test_misassigned_group_caught_when_grand_totals_match(self):
        orders = Table((
            ('CN', 'CNY', Decimal('100.00')),
            ('US', 'CNY', Decimal('50.00')),
        ), ['region', 'currency', 'sales'], [self.text_type, self.text_type, self.number_type])

        refunds = Table((
            ('CN', 'CNY', Decimal('10.00')),
        ), ['region', 'currency', 'refund'], [self.text_type, self.text_type, self.number_type])

        settlements = Table((
            ('CN', 'CNY', Decimal('140.00')),
        ), ['region', 'currency', 'settled'], [self.text_type, self.text_type, self.number_type])

        result = self.check.run([orders, refunds, settlements], group_by=['region', 'currency'])

        failing_keys = [failure.group_key for failure in result.failures]

        self.assertIn(('CN', 'CNY'), failing_keys)
        self.assertIn(('US', 'CNY'), failing_keys)

        by_key = {failure.group_key: failure for failure in result.failures}

        self.assertEqual(by_key[('CN', 'CNY')].difference, Decimal('-50.00'))
        self.assertIsNone(by_key[('US', 'CNY')].difference)

    def test_null_values_are_skipped_not_zeroed(self):
        orders = Table((
            ('CN', 'CNY', Decimal('100.00')),
            ('CN', 'CNY', None),
        ), ['region', 'currency', 'sales'], [self.text_type, self.text_type, self.number_type])

        refunds = Table((
            ('CN', 'CNY', Decimal('10.00')),
        ), ['region', 'currency', 'refund'], [self.text_type, self.text_type, self.number_type])

        settlements = Table((
            ('CN', 'CNY', Decimal('90.00')),
        ), ['region', 'currency', 'settled'], [self.text_type, self.text_type, self.number_type])

        result = self.check.run([orders, refunds, settlements], group_by=['region', 'currency'])

        self.assertTrue(result.passed)

    def test_single_table_column_name_metrics(self):
        table = Table((
            ('CN', Decimal('150.00'), Decimal('10.00'), Decimal('140.00')),
        ), ['region', 'sales', 'refund', 'settled'],
            [self.text_type, self.number_type, self.number_type, self.number_type])

        check = Balance(
            left_metrics=[('sales', 'sales')],
            right_metrics=[('refund', 'refund'), ('settled', 'settled')],
            tolerance=Decimal('0'),
        )

        result = table.run(check, group_by='region')

        self.assertTrue(result.passed)
        self.assertEqual(result.failures, ())

    def test_table_run_without_group_by(self):
        table = Table((
            (Decimal('150.00'), Decimal('10.00'), Decimal('140.00')),
        ), ['sales', 'refund', 'settled'],
            [self.number_type, self.number_type, self.number_type])

        check = Balance(
            left_metrics=[('sales', 'sales')],
            right_metrics=[('refund', 'refund'), ('settled', 'settled')],
            tolerance=Decimal('0'),
        )

        result = table.run(check)

        self.assertTrue(result.passed)
        self.assertEqual(result.failures, ())

    def test_tableset_run(self):
        orders = Table((
            ('CN', Decimal('150.00')),
            ('US', Decimal('500.00')),
        ), ['region', 'sales'], [self.text_type, self.number_type])

        settlements = Table((
            ('CN', Decimal('150.00')),
            ('US', Decimal('480.00')),
        ), ['region', 'settled'], [self.text_type, self.number_type])

        check = Balance(
            left_metrics=[('sales', (0, 'sales'))],
            right_metrics=[('settled', (1, 'settled'))],
            tolerance=Decimal('0'),
        )

        result = check.run([
            orders.group_by('region'),
            settlements.group_by('region'),
        ])

        self.assertEqual(len(result.failures), 1)
        self.assertEqual(result.failures[0].group_key, ('US',))
        self.assertEqual(result.failures[0].difference, Decimal('20.00'))

    def test_nested_tableset_composite_keys(self):
        result = self.check.run(self.tables, group_by=['region', 'currency'])

        keys = set()

        for table in (self.orders, self.refunds, self.settlements):
            grouped = table.group_by('region').group_by('currency')
            for region, by_currency in grouped.items():
                for currency in by_currency.keys():
                    keys.add((region, currency))

        nested = [
            table.group_by('region').group_by('currency')
            for table in self.tables
        ]

        nested_result = self.check.run(nested)

        self.assertTrue(nested_result.passed)
        self.assertEqual({failure.group_key for failure in nested_result.failures}, set())
        self.assertEqual(keys, {('CN', 'CNY'), ('US', 'USD')})

    def test_group_by_function(self):
        table = Table((
            ('CN-east', Decimal('150.00'), Decimal('140.00')),
            ('CN-west', Decimal('50.00'), Decimal('50.00')),
        ), ['place', 'sales', 'settled'], [self.text_type, self.number_type, self.number_type])

        check = Balance(
            left_metrics=[('sales', 'sales')],
            right_metrics=[('settled', 'settled')],
            tolerance=Decimal('0'),
        )

        result = table.run(check, group_by=lambda row: row['place'].split('-')[0])

        self.assertEqual(len(result.failures), 1)
        self.assertEqual(result.failures[0].group_key, ('CN',))
        self.assertEqual(result.failures[0].difference, Decimal('10.00'))

    def test_non_number_column_raises(self):
        check = Balance(
            left_metrics=[('region', (0, 'region'))],
            right_metrics=[('settled', (2, 'settled'))],
            tolerance=Decimal('0'),
        )

        with self.assertRaises(DataTypeError):
            check.run(self.tables, group_by='region')

    def test_duplicate_metric_names_rejected(self):
        with self.assertRaises(ValueError):
            Balance(
                left_metrics=[('same', (0, 'sales')), ('same', (1, 'refund'))],
                right_metrics=[('settled', (2, 'settled'))],
                tolerance=Decimal('0'),
            )

    def test_shared_metric_names_rejected(self):
        with self.assertRaises(ValueError):
            Balance(
                left_metrics=[('amount', (0, 'sales'))],
                right_metrics=[('amount', (2, 'settled'))],
                tolerance=Decimal('0'),
            )

    def test_invalid_table_index_rejected(self):
        check = Balance(
            left_metrics=[('sales', (3, 'sales'))],
            right_metrics=[('settled', (2, 'settled'))],
            tolerance=Decimal('0'),
        )

        with self.assertRaises(ValueError):
            check.run(self.tables, group_by='region')

    def test_tolerance_is_required(self):
        with self.assertRaises(TypeError):
            Balance(
                left_metrics=[('sales', (0, 'sales'))],
                right_metrics=[('settled', (2, 'settled'))],
            )

    def test_default_name_is_class_name(self):
        check = Balance(
            left_metrics=[('sales', (0, 'sales'))],
            right_metrics=[('settled', (2, 'settled'))],
            tolerance=Decimal('0'),
        )

        self.assertEqual(check.name, 'Balance')
        self.assertIn('Balance', str(check))

    def test_result_strings(self):
        passing = self.check.run(self.tables, group_by=['region', 'currency'])

        self.assertIn('passed', str(passing))

        settlements = Table((
            ('CN', 'CNY', Decimal('140.00')),
            ('US', 'USD', Decimal('470.00')),
        ), ['region', 'currency', 'settled'], [self.text_type, self.text_type, self.number_type])

        failing = self.check.run([self.orders, self.refunds, settlements],
                                 group_by=['region', 'currency'])

        self.assertIn('1 failing group(s)', str(failing))
        self.assertIn("('US', 'USD')", str(failing.failures[0]))

    def test_end_to_end_daily_report_narrative(self):
        orders = Table((
            ('CN', 'CNY', Decimal('1000.00')),
            ('US', 'USD', Decimal('2000.00')),
            ('EU', 'EUR', Decimal('500.00')),
        ), ['region', 'currency', 'sales'], [self.text_type, self.text_type, self.number_type])

        refunds = Table((
            ('CN', 'CNY', Decimal('100.00')),
            ('US', 'USD', Decimal('200.00')),
        ), ['region', 'currency', 'refund'], [self.text_type, self.text_type, self.number_type])

        settlements = Table((
            ('CN', 'CNY', Decimal('900.00')),
            ('US', 'USD', Decimal('1700.00')),
        ), ['region', 'currency', 'settled'], [self.text_type, self.text_type, self.number_type])

        check = Balance(
            left_metrics=[('sales', (0, 'sales'))],
            right_metrics=[('refund', (1, 'refund')), ('settled', (2, 'settled'))],
            tolerance=Decimal('0.01'),
            name='daily report settlement',
        )

        result = check.run([orders, refunds, settlements], group_by=['region', 'currency'])

        self.assertEqual(len(result.failures), 2)

        failing_keys = {failure.group_key for failure in result.failures}

        self.assertEqual(failing_keys, {('US', 'USD'), ('EU', 'EUR')})

        by_key = {failure.group_key: failure for failure in result.failures}

        self.assertEqual(by_key[('US', 'USD')].difference, Decimal('100.00'))
        self.assertIsNone(by_key[('EU', 'EUR')].difference)
        self.assertIsNone(by_key[('EU', 'EUR')].values['refund'])

    def test_agate_namespace_exports(self):
        self.assertIs(agate.Balance, Balance)
        self.assertIs(agate.CheckResult, CheckResult)
        self.assertIs(agate.CheckFailure, CheckFailure)
        self.assertTrue(hasattr(Table, 'run'))
        self.assertTrue(hasattr(TableSet, 'run'))

    def test_mixed_grouped_and_ungrouped_tables_rejected(self):
        check = Balance(
            left_metrics=[('sales', (0, 'sales'))],
            right_metrics=[('settled', (1, 'settled'))],
            tolerance=Decimal('0'),
        )

        with self.assertRaises(ValueError):
            check.run([
                self.orders,
                self.settlements.group_by('region'),
            ])

    def test_empty_table_list_rejected(self):
        check = Balance(
            left_metrics=[('sales', (0, 'sales'))],
            right_metrics=[('settled', (0, 'settled'))],
            tolerance=Decimal('0'),
        )

        with self.assertRaises(ValueError):
            check.run([])

    def test_custom_check_subclass(self):
        from agate.checks import Check

        class RatioWithin(Check):
            def __init__(self, numerator, denominator, tolerance):
                super().__init__('ratio within one')
                self._numerator = numerator
                self._denominator = denominator
                self._tolerance_value = tolerance

            def metrics(self):
                return [('numerator', self._numerator), ('denominator', self._denominator)]

            def difference(self, values):
                return values['numerator'] - values['denominator']

            @property
            def _tolerance(self):
                return self._tolerance_value

        table = Table((
            ('CN', Decimal('100.00'), Decimal('105.00')),
        ), ['region', 'sales', 'settled'], [self.text_type, self.number_type, self.number_type])

        check = RatioWithin('sales', 'settled', Decimal('10.00'))

        self.assertTrue(table.run(check, group_by='region').passed)

        strict = RatioWithin('sales', 'settled', Decimal('4.00'))
        result = table.run(strict, group_by='region')

        self.assertFalse(result.passed)
        self.assertEqual(result.failures[0].difference, Decimal('-5.00'))
