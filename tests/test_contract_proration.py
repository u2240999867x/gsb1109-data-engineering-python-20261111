import unittest
from datetime import date, timedelta
from decimal import Decimal

import agate
from contract_proration import (
    PeriodCoverageError,
    assert_reconciled,
    monthly_periods,
    prorate_contracts,
    reconcile_proration,
)


def build_contracts(rows):
    return agate.Table(
        rows,
        ('contract_id', 'vendor', 'start_date', 'end_date', 'amount'),
        [agate.Text(), agate.Text(), agate.Date(), agate.Date(), agate.Number()],
    )


class TestMonthlyPeriods(unittest.TestCase):
    def test_generates_inclusive_calendar_months(self):
        periods = monthly_periods(date(2024, 1, 15), date(2024, 3, 10))

        self.assertEqual(
            periods,
            [
                ('2024-01', date(2024, 1, 1), date(2024, 1, 31)),
                ('2024-02', date(2024, 2, 1), date(2024, 2, 29)),
                ('2024-03', date(2024, 3, 1), date(2024, 3, 31)),
            ],
        )

    def test_rejects_reversed_span(self):
        with self.assertRaises(ValueError):
            monthly_periods(date(2024, 2, 1), date(2024, 1, 1))


class TestProrateContracts(unittest.TestCase):
    def test_mid_month_contract_prorates_by_covered_days(self):
        contracts = build_contracts([
            ('C-1', 'Acme', date(2024, 1, 15), date(2024, 3, 10), Decimal('1000.00')),
        ])
        periods = monthly_periods(date(2024, 1, 15), date(2024, 3, 10))

        result = prorate_contracts(
            contracts, 'contract_id', 'start_date', 'end_date', 'amount', periods
        )

        self.assertColumnStructure(result)
        self.assertEqual(len(result.rows), 3)
        self.assertEqual(
            [row['period'] for row in result.rows],
            ['2024-01', '2024-02', '2024-03'],
        )
        self.assertEqual(
            [row['covered_days'] for row in result.rows],
            [Decimal('17'), Decimal('29'), Decimal('10')],
        )
        self.assertEqual(
            [row['allocated_amount'] for row in result.rows],
            [Decimal('303.57'), Decimal('517.86'), Decimal('178.57')],
        )
        self.assertEqual(
            sum((row['allocated_amount'] for row in result.rows), Decimal('0')),
            Decimal('1000.00'),
        )
        self.assertTrue(all(row['vendor'] == 'Acme' for row in result.rows))
        self.assertTrue(all(row['is_remainder_row'] is False for row in result.rows))

    def test_tail_difference_is_flagged_on_last_period(self):
        contracts = build_contracts([
            ('C-2', 'Acme', date(2024, 1, 31), date(2024, 3, 1), Decimal('100.00')),
        ])
        periods = monthly_periods(date(2024, 1, 31), date(2024, 3, 1))

        result = prorate_contracts(
            contracts, 'contract_id', 'start_date', 'end_date', 'amount', periods
        )

        # 1, 29 and 1 covered days; independently rounded shares sum to 100.01,
        # so one cent is moved off the final period.
        self.assertEqual(
            [row['allocated_amount'] for row in result.rows],
            [Decimal('3.23'), Decimal('93.55'), Decimal('3.22')],
        )
        self.assertEqual(
            [row['is_remainder_row'] for row in result.rows],
            [False, False, True],
        )
        self.assertEqual(
            sum((row['allocated_amount'] for row in result.rows), Decimal('0')),
            Decimal('100.00'),
        )

    def test_tail_difference_can_go_to_first_period(self):
        contracts = build_contracts([
            ('C-3', 'Acme', date(2024, 1, 31), date(2024, 3, 1), Decimal('100.00')),
        ])
        periods = monthly_periods(date(2024, 1, 31), date(2024, 3, 1))

        result = prorate_contracts(
            contracts, 'contract_id', 'start_date', 'end_date', 'amount', periods,
            remainder_to='first',
        )

        self.assertEqual(
            [row['allocated_amount'] for row in result.rows],
            [Decimal('3.22'), Decimal('93.55'), Decimal('3.23')],
        )
        self.assertEqual(
            [row['is_remainder_row'] for row in result.rows],
            [True, False, False],
        )

    def test_largest_remainder_strategy_on_single_day_periods(self):
        days = [date(2024, 4, 1) + timedelta(days=i) for i in range(6)]
        periods = [('day-%d' % i, day, day) for i, day in enumerate(days)]
        contracts = build_contracts([
            ('C-4', 'Acme', days[0], days[-1], Decimal('100.00')),
        ])

        result = prorate_contracts(
            contracts, 'contract_id', 'start_date', 'end_date', 'amount', periods,
            remainder_to='largest_remainder',
        )

        # Each floor share is 16.66 (99.96 total); the four leftover cents go
        # to the four equally-largest fractional remainders, ties broken first.
        self.assertEqual(
            [row['allocated_amount'] for row in result.rows],
            [Decimal('16.67'), Decimal('16.67'), Decimal('16.67'),
             Decimal('16.67'), Decimal('16.66'), Decimal('16.66')],
        )
        self.assertEqual(
            sum((row['allocated_amount'] for row in result.rows), Decimal('0')),
            Decimal('100.00'),
        )
        self.assertEqual(
            [row['is_remainder_row'] for row in result.rows],
            [True, True, True, True, False, False],
        )

    def test_custom_zero_precision(self):
        days = [date(2024, 4, 1) + timedelta(days=i) for i in range(3)]
        periods = [(day.isoformat(), day, day) for day in days]
        contracts = build_contracts([
            ('C-5', 'Acme', days[0], days[-1], Decimal('100')),
        ])

        result = prorate_contracts(
            contracts, 'contract_id', 'start_date', 'end_date', 'amount', periods,
            precision=0,
        )

        self.assertEqual(
            [row['allocated_amount'] for row in result.rows],
            [Decimal('33'), Decimal('33'), Decimal('34')],
        )
        self.assertEqual(
            sum((row['allocated_amount'] for row in result.rows), Decimal('0')),
            Decimal('100'),
        )

    def test_higher_precision_keeps_exact_total(self):
        days = [date(2024, 4, 1) + timedelta(days=i) for i in range(6)]
        periods = [(day.isoformat(), day, day) for day in days]
        contracts = build_contracts([
            ('C-6', 'Acme', days[0], days[-1], Decimal('100.000')),
        ])

        result = prorate_contracts(
            contracts, 'contract_id', 'start_date', 'end_date', 'amount', periods,
            precision=3,
        )

        # Each share rounds to 16.667 (100.002 total); two mills are moved off
        # the final two rows and both are flagged, so the total stays exact.
        self.assertEqual(
            [row['allocated_amount'] for row in result.rows],
            [Decimal('16.667'), Decimal('16.667'), Decimal('16.667'),
             Decimal('16.667'), Decimal('16.666'), Decimal('16.666')],
        )
        self.assertEqual(
            [row['is_remainder_row'] for row in result.rows],
            [False, False, False, False, True, True],
        )
        self.assertEqual(
            sum((row['allocated_amount'] for row in result.rows), Decimal('0')),
            Decimal('100.000'),
        )

    def test_negative_amount_allocates_absolute_then_restores_sign(self):
        days = [date(2024, 4, 1) + timedelta(days=i) for i in range(3)]
        periods = [(day.isoformat(), day, day) for day in days]
        contracts = build_contracts([
            ('C-7', 'Acme', days[0], days[-1], Decimal('-100.00')),
        ])

        result = prorate_contracts(
            contracts, 'contract_id', 'start_date', 'end_date', 'amount', periods
        )

        self.assertEqual(
            [row['allocated_amount'] for row in result.rows],
            [Decimal('-33.33'), Decimal('-33.33'), Decimal('-33.34')],
        )
        self.assertEqual(
            sum((row['allocated_amount'] for row in result.rows), Decimal('0')),
            Decimal('-100.00'),
        )

    def test_input_amount_finer_than_precision_is_quantized(self):
        days = [date(2024, 4, 1) + timedelta(days=i) for i in range(3)]
        periods = [(day.isoformat(), day, day) for day in days]
        contracts = build_contracts([
            ('C-8', 'Acme', days[0], days[-1], Decimal('99.999')),
        ])

        result = prorate_contracts(
            contracts, 'contract_id', 'start_date', 'end_date', 'amount', periods,
            precision=2,
        )

        self.assertEqual(
            sum((row['allocated_amount'] for row in result.rows), Decimal('0')),
            Decimal('100.00'),
        )

    def test_multiple_contracts_keep_identity_and_reconcile(self):
        contracts = build_contracts([
            ('C-1', 'Acme', date(2024, 1, 15), date(2024, 3, 10), Decimal('1000.00')),
            ('C-2', 'Beta', date(2024, 1, 31), date(2024, 3, 1), Decimal('100.00')),
        ])
        periods = monthly_periods(date(2024, 1, 15), date(2024, 3, 10))

        result = prorate_contracts(
            contracts, 'contract_id', 'start_date', 'end_date', 'amount', periods
        )

        self.assertEqual(len(result.rows), 6)
        reconciliation = reconcile_proration(
            result, contracts, 'contract_id', 'start_date', 'end_date', 'amount'
        )
        assert_reconciled(reconciliation)

        self.assertEqual(
            [row['amount_match'] for row in reconciliation.rows], [True, True]
        )
        self.assertEqual(
            [row['days_match'] for row in reconciliation.rows], [True, True]
        )

    def test_shared_grid_can_extend_beyond_each_contract(self):
        contracts = build_contracts([
            ('C-13', 'Acme', date(2024, 2, 10), date(2024, 2, 20), Decimal('110.00')),
        ])
        grid = monthly_periods(date(2024, 1, 1), date(2024, 3, 31))

        result = prorate_contracts(
            contracts, 'contract_id', 'start_date', 'end_date', 'amount', grid
        )

        self.assertEqual([row['period'] for row in result.rows], ['2024-02'])
        self.assertEqual([row['covered_days'] for row in result.rows], [Decimal('11')])
        self.assertEqual(
            [row['allocated_amount'] for row in result.rows], [Decimal('110.00')]
        )

    def test_periods_accept_mapping_input(self):
        start, end = date(2024, 1, 1), date(2024, 1, 2)
        contracts = build_contracts([
            ('C-14', 'Acme', start, end, Decimal('10.00')),
        ])
        periods = [
            {'period': 'day-1', 'start': start, 'end': start},
            {'period': 'day-2', 'start': end, 'end': end},
        ]

        result = prorate_contracts(
            contracts, 'contract_id', 'start_date', 'end_date', 'amount', periods
        )

        self.assertEqual(
            [row['allocated_amount'] for row in result.rows],
            [Decimal('5.00'), Decimal('5.00')],
        )

    def test_overlapping_periods_are_rejected(self):
        contracts = build_contracts([
            ('C-9', 'Acme', date(2024, 1, 1), date(2024, 2, 28), Decimal('100.00')),
        ])
        periods = [
            ('2024-01', date(2024, 1, 1), date(2024, 1, 31)),
            ('2024-02', date(2024, 1, 31), date(2024, 2, 28)),
        ]

        with self.assertRaises(PeriodCoverageError):
            prorate_contracts(
                contracts, 'contract_id', 'start_date', 'end_date', 'amount', periods
            )

    def test_incomplete_coverage_is_rejected(self):
        contracts = build_contracts([
            ('C-10', 'Acme', date(2024, 1, 1), date(2024, 3, 31), Decimal('100.00')),
        ])
        periods = [
            ('2024-01', date(2024, 1, 1), date(2024, 1, 31)),
            ('2024-02', date(2024, 2, 1), date(2024, 2, 29)),
        ]

        with self.assertRaises(PeriodCoverageError):
            prorate_contracts(
                contracts, 'contract_id', 'start_date', 'end_date', 'amount', periods
            )

    def test_gap_between_periods_is_rejected(self):
        contracts = build_contracts([
            ('C-11', 'Acme', date(2024, 1, 1), date(2024, 3, 31), Decimal('100.00')),
        ])
        periods = [
            ('2024-01', date(2024, 1, 1), date(2024, 1, 30)),
            ('2024-03', date(2024, 3, 1), date(2024, 3, 31)),
        ]

        with self.assertRaises(PeriodCoverageError):
            prorate_contracts(
                contracts, 'contract_id', 'start_date', 'end_date', 'amount', periods
            )

    def test_invalid_strategy_is_rejected(self):
        contracts = build_contracts([
            ('C-12', 'Acme', date(2024, 1, 1), date(2024, 1, 31), Decimal('100.00')),
        ])
        periods = monthly_periods(date(2024, 1, 1), date(2024, 1, 31))

        with self.assertRaises(ValueError):
            prorate_contracts(
                contracts, 'contract_id', 'start_date', 'end_date', 'amount', periods,
                remainder_to='nowhere',
            )

    def assertColumnStructure(self, table):
        self.assertIsInstance(table, agate.Table)
        self.assertIn('contract_id', table.column_names)
        self.assertIn('covered_days', table.column_names)
        self.assertIn('raw_allocated_amount', table.column_names)
        self.assertIn('allocated_amount', table.column_names)
        self.assertIn('is_remainder_row', table.column_names)
        self.assertIsInstance(table.columns['allocated_amount'].data_type, agate.Number)
        self.assertIsInstance(table.columns['is_remainder_row'].data_type, agate.Boolean)
        self.assertIsInstance(table.columns['period_start'].data_type, agate.Date)


if __name__ == '__main__':
    unittest.main()
