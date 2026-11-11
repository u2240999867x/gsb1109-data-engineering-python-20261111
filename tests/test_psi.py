import unittest
import warnings
from decimal import Decimal

import agate
from agate.aggregations import (DEFAULT_SMOOTHING, MISSING_EXCLUDE, MISSING_SEPARATE, MISSING_UNKNOWN, PSI,
                                PSIBaseline, PSI_STATUS_EMPTY_BATCH, PSI_STATUS_OK)
from agate.aggregations.psi import _EXCLUDE
from agate.data_types import Number, Text
from agate.exceptions import DataTypeError, UnsupportedAggregationError
from agate.warns import PSIEmptyBatchWarning, PSINewCategoryWarning


class TestPSIBaselineCategorical(unittest.TestCase):
    def setUp(self):
        self.table = agate.Table(
            (('a',), ('a',), ('b',), (None,)),
            ('cat',),
            (Text(),))

    def test_declares_fixed_categories_and_unknown_bin(self):
        baseline = PSIBaseline.from_categories('cat', self.table.columns['cat'].values(), ('a', 'b', 'c'))

        self.assertEqual(baseline.kind, 'categorical')
        self.assertEqual(baseline.bin_labels, ('a', 'b', 'c', '__unknown__', None))
        self.assertEqual(baseline.counts, {'a': 2, 'b': 1, 'c': 0, '__unknown__': 0, None: 1})
        self.assertEqual(baseline.total_count, 4)
        self.assertEqual(baseline.smoothing, DEFAULT_SMOOTHING)
        self.assertEqual(baseline.missing_policy, MISSING_SEPARATE)

    def test_category_absent_from_baseline_keeps_zero_count_bin(self):
        baseline = PSIBaseline.from_categories('cat', self.table.columns['cat'].values(), ('a', 'b'))

        self.assertEqual(baseline.counts['b'], 1)
        self.assertIn('__unknown__', baseline.counts)

    def test_new_category_routes_to_unknown(self):
        baseline = PSIBaseline.from_categories('cat', self.table.columns['cat'].values(), ('a', 'b'))

        self.assertEqual(baseline.assign('never-seen'), '__unknown__')
        self.assertEqual(baseline.assign('a'), 'a')

    def test_missing_policy_exclude(self):
        baseline = PSIBaseline.from_categories(
            'cat', self.table.columns['cat'].values(), ('a', 'b'), missing_policy=MISSING_EXCLUDE)

        self.assertNotIn(None, baseline.bin_keys)
        self.assertEqual(baseline.total_count, 3)
        self.assertIs(baseline.assign(None), _EXCLUDE)

    def test_missing_policy_unknown(self):
        baseline = PSIBaseline.from_categories(
            'cat', self.table.columns['cat'].values(), ('a', 'b'), missing_policy=MISSING_UNKNOWN)

        self.assertNotIn(None, baseline.bin_keys)
        self.assertEqual(baseline.counts['__unknown__'], 1)
        self.assertEqual(baseline.assign(None), '__unknown__')

    def test_config_records_the_full_declaration(self):
        baseline = PSIBaseline.from_categories(
            'cat', self.table.columns['cat'].values(), ('a', 'b'), smoothing=1)

        config = baseline.config

        self.assertEqual(config['column_name'], 'cat')
        self.assertEqual(config['kind'], 'categorical')
        self.assertEqual(config['categories'], ('a', 'b'))
        self.assertEqual(config['smoothing'], Decimal('1'))
        self.assertEqual(config['missing_policy'], MISSING_SEPARATE)
        self.assertEqual(config['unknown_label'], '__unknown__')
        self.assertEqual(config['baseline_total'], 4)

    def test_invalid_declarations(self):
        with self.assertRaises(ValueError):
            PSIBaseline.from_categories('cat', ('a',), ())
        with self.assertRaises(ValueError):
            PSIBaseline.from_categories('cat', ('a', 'a'), ('a', 'a'))
        with self.assertRaises(ValueError):
            PSIBaseline.from_categories('cat', ('a',), ('x',), unknown_label='x')
        with self.assertRaises(ValueError):
            PSIBaseline.from_categories('cat', ('a',), ('a',), missing_policy='bogus')
        with self.assertRaises(ValueError):
            PSIBaseline.from_categories('cat', ('a',), ('a',), smoothing=-1)
        with self.assertRaises(ValueError):
            PSIBaseline.from_categories('cat', (None,), ('a',), missing_policy=MISSING_EXCLUDE)
        with self.assertRaises(ValueError):
            PSIBaseline.from_categories('cat', ('a',), ('a', None))
        with self.assertRaises(ValueError):
            PSIBaseline.from_categories('cat', ('a',), ('a',), unknown_label=None)
        with self.assertRaises(ValueError):
            PSIBaseline.from_bins('x', (1,), (0, 10), unknown_label=None)

    def test_counts_are_copied(self):
        baseline = PSIBaseline.from_categories('cat', self.table.columns['cat'].values(), ('a', 'b'))
        counts = baseline.counts
        counts['a'] = 999

        self.assertEqual(baseline.counts['a'], 2)


class TestPSIBaselineNumeric(unittest.TestCase):
    def setUp(self):
        self.table = agate.Table(
            ((1,), (9,), (10,), (20,), (25,), (50,), (None,)),
            ('x',),
            (Number(),))
        self.values = self.table.columns['x'].values()

    def test_declares_bins_from_boundaries(self):
        baseline = PSIBaseline.from_bins('x', self.values, (0, 10, 20, 30))

        self.assertEqual(baseline.kind, 'numeric')
        self.assertEqual(baseline.bin_labels, ('[0, 10)', '[10, 20)', '[20, 30]', '__unknown__', None))
        self.assertEqual(baseline.counts[(Decimal(0), Decimal(10))], 2)
        self.assertEqual(baseline.counts[(Decimal(10), Decimal(20))], 1)
        self.assertEqual(baseline.counts[(Decimal(20), Decimal(30))], 2)
        self.assertEqual(baseline.counts['__unknown__'], 1)
        self.assertEqual(baseline.counts[None], 1)

    def test_boundary_values_follow_half_open_intervals(self):
        baseline = PSIBaseline.from_bins('x', self.values, (0, 10, 20, 30))

        self.assertEqual(baseline.assign(0), (Decimal(0), Decimal(10)))
        self.assertEqual(baseline.assign(10), (Decimal(10), Decimal(20)))
        self.assertEqual(baseline.assign(30), (Decimal(20), Decimal(30)))
        self.assertEqual(baseline.assign(-1), '__unknown__')
        self.assertEqual(baseline.assign(31), '__unknown__')

    def test_accepts_float_boundaries(self):
        baseline = PSIBaseline.from_bins('x', self.values, (0.0, 2.5, 5.0))

        self.assertEqual(baseline.bin_labels, ('[0.0, 2.5)', '[2.5, 5.0]', '__unknown__', None))

    def test_missing_policies(self):
        excluded = PSIBaseline.from_bins('x', self.values, (0, 10, 20, 30), missing_policy=MISSING_EXCLUDE)
        self.assertNotIn(None, excluded.bin_keys)
        self.assertEqual(excluded.total_count, 6)

        unknown = PSIBaseline.from_bins('x', self.values, (0, 10, 20, 30), missing_policy=MISSING_UNKNOWN)
        self.assertEqual(unknown.counts['__unknown__'], 2)

    def test_invalid_boundaries(self):
        with self.assertRaises(ValueError):
            PSIBaseline.from_bins('x', self.values, (10,))
        with self.assertRaises(ValueError):
            PSIBaseline.from_bins('x', self.values, (10, 10, 20))
        with self.assertRaises(ValueError):
            PSIBaseline.from_bins('x', self.values, (20, 10))


class TestPSICategorical(unittest.TestCase):
    def setUp(self):
        baseline_table = agate.Table(
            (('a',), ('a',), ('a',), ('a',), ('b',)),
            ('cat',),
            (Text(),))
        self.baseline = PSIBaseline.from_categories(
            'cat', baseline_table.columns['cat'].values(), ('a', 'b'))

    def test_identical_distribution_has_zero_psi(self):
        batch = agate.Table((('a',), ('a',), ('a',), ('a',), ('b',)), ('cat',), (Text(),))

        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            result = batch.aggregate(PSI(self.baseline))

        self.assertEqual(result.status, PSI_STATUS_OK)
        self.assertEqual(result.psi, Decimal('0.0'))
        self.assertTrue(all(contribution == 0 for contribution in result.contributions.values()))

    def test_hand_computed_value_and_bin_contributions(self):
        # Baseline a=4, b=1; batch a=1, b=4. With four bins and smoothing 0.5
        # both adjusted totals are 7. Only a and b move, each by 3/7, so the
        # total PSI is (6 / 7) * ln(3); the unknown and missing bins contribute 0.
        batch = agate.Table((('a',), ('b',), ('b',), ('b',), ('b',)), ('cat',), (Text(),))

        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            result = batch.aggregate(PSI(self.baseline))

        expected = Decimal('6') / Decimal('7') * Decimal('3').ln()
        self.assertAlmostEqual(float(result.psi), float(expected), places=12)
        self.assertAlmostEqual(float(sum(result.contributions.values(), Decimal('0'))),
                               float(result.psi), places=12)
        self.assertEqual(result.contributions['__unknown__'], Decimal('0'))
        self.assertEqual(result.contributions[None], Decimal('0'))

    def test_result_table_lists_every_fixed_bin(self):
        batch = agate.Table((('a',), ('b',)), ('cat',), (Text(),))

        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            result = batch.aggregate(PSI(self.baseline))

        table = result.table
        self.assertEqual(table.column_names,
                         ('bin', 'baseline_count', 'batch_count', 'baseline_proportion',
                          'batch_proportion', 'psi_contribution'))
        self.assertEqual([row['bin'] for row in table.rows], list(self.baseline.bin_labels))
        self.assertEqual([row['batch_count'] for row in table.rows], [1, 1, 0, 0])
        self.assertTrue(all(isinstance(row['psi_contribution'], Decimal) for row in table.rows))

    def test_zero_frequency_bin_is_kept_and_flagged(self):
        batch = agate.Table((('a',), ('a',)), ('cat',), (Text(),))

        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            result = batch.aggregate(PSI(self.baseline))

        self.assertEqual(result.zero_frequency_bins, ('b', '__unknown__', None))
        unknown_row = result.table.where(lambda row: row['bin'] == '__unknown__').rows[0]
        self.assertEqual(unknown_row['batch_count'], 0)
        self.assertGreater(unknown_row['batch_proportion'], 0)
        self.assertEqual(result.smoothing, DEFAULT_SMOOTHING)

    def test_new_category_warns_and_is_counted_as_unknown(self):
        batch = agate.Table((('a',), ('new',)), ('cat',), (Text(),))

        with self.assertWarns(PSINewCategoryWarning):
            result = batch.aggregate(PSI(self.baseline))

        self.assertTrue(result.has_unknown_values)
        self.assertEqual(result.table.where(lambda row: row['bin'] == '__unknown__').rows[0]['batch_count'], 1)

    def test_known_categories_do_not_warn(self):
        batch = agate.Table((('a',), ('b',)), ('cat',), (Text(),))

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            batch.aggregate(PSI(self.baseline))

        self.assertFalse(any(item.category is PSINewCategoryWarning for item in caught))

    def test_empty_batch_is_inconclusive_not_zero_drift(self):
        baseline = PSIBaseline.from_categories(
            'cat', ('a', 'a', 'b'), ('a', 'b'), missing_policy=MISSING_EXCLUDE)
        batch = agate.Table(((None,), (None,)), ('cat',), (Text(),))

        with self.assertWarns(PSIEmptyBatchWarning):
            result = batch.aggregate(PSI(baseline))

        self.assertEqual(result.status, PSI_STATUS_EMPTY_BATCH)
        self.assertIsNone(result.psi)
        self.assertEqual(result.batch_total, 0)
        self.assertTrue(all(value is None for value in result.contributions.values()))
        self.assertTrue(all(row['psi_contribution'] is None for row in result.table.rows))
        self.assertIn('inconclusive', str(result))

    def test_zero_row_batch_is_inconclusive(self):
        batch = agate.Table((), ('cat',), (Text(),))

        with self.assertWarns(PSIEmptyBatchWarning):
            result = batch.aggregate(PSI(self.baseline))

        self.assertEqual(result.status, PSI_STATUS_EMPTY_BATCH)
        self.assertIsNone(result.psi)

    def test_missing_policies_are_applied_to_batches(self):
        excluded = PSIBaseline.from_categories(
            'cat', ('a', 'a', 'b'), ('a', 'b'), missing_policy=MISSING_EXCLUDE)
        batch = agate.Table((('a',), (None,)), ('cat',), (Text(),))

        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            result = batch.aggregate(PSI(excluded))

        self.assertEqual(result.batch_total, 1)

        unknown = PSIBaseline.from_categories(
            'cat', ('a', 'a', 'b'), ('a', 'b'), missing_policy=MISSING_UNKNOWN)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            result = batch.aggregate(PSI(unknown))

        self.assertEqual(result.batch_total, 2)
        self.assertEqual(result.table.where(lambda row: row['bin'] == '__unknown__').rows[0]['batch_count'], 1)

    def test_validate_rejects_numeric_baseline_on_text_column(self):
        numeric_baseline = PSIBaseline.from_bins('cat', (1, 2), (0, 10))

        with self.assertRaises(DataTypeError):
            PSI(numeric_baseline).validate(agate.Table((('a',),), ('cat',), (Text(),)))

    def test_can_not_be_applied_to_tableset(self):
        with self.assertRaises(UnsupportedAggregationError):
            PSI(self.baseline).get_aggregate_data_type(None)

    def test_requires_baseline_instance(self):
        with self.assertRaises(ValueError):
            PSI(object())


class TestPSINumeric(unittest.TestCase):
    def test_numeric_psi_flow(self):
        baseline_table = agate.Table(((1,), (5,), (15,), (25,)), ('x',), (Number(),))
        baseline = PSIBaseline.from_bins('x', baseline_table.columns['x'].values(), (0, 10, 20, 30))
        batch = agate.Table(((5,), (25,), (40,)), ('x',), (Number(),))

        with self.assertWarns(PSINewCategoryWarning):
            result = batch.aggregate(PSI(baseline))

        self.assertEqual(result.status, PSI_STATUS_OK)
        self.assertIsInstance(result.psi, Decimal)
        self.assertEqual(result.batch_total, 3)
        self.assertTrue(result.has_unknown_values)
        self.assertEqual(
            [row['batch_count'] for row in result.table.rows],
            [1, 0, 1, 1, 0])
        self.assertIn('[10, 20)', result.zero_frequency_bins)

    def test_numeric_empty_batch(self):
        baseline_table = agate.Table(((1,), (5,), (15,)), ('x',), (Number(),))
        baseline = PSIBaseline.from_bins(
            'x', baseline_table.columns['x'].values(), (0, 10, 20, 30), missing_policy=MISSING_EXCLUDE)
        batch = agate.Table(((None,),), ('x',), (Number(),))

        with self.assertWarns(PSIEmptyBatchWarning):
            result = batch.aggregate(PSI(baseline))

        self.assertEqual(result.status, PSI_STATUS_EMPTY_BATCH)
        self.assertIsNone(result.psi)


class TestPSISmoothing(unittest.TestCase):
    def test_explicit_smoothing_is_recorded_and_applied(self):
        baseline_table = agate.Table((('a',), ('b',)), ('cat',), (Text(),))
        baseline = PSIBaseline.from_categories(
            'cat', baseline_table.columns['cat'].values(), ('a', 'b'), smoothing=1)
        batch = agate.Table((('a',), ('a',)), ('cat',), (Text(),))

        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            result = batch.aggregate(PSI(baseline))

        self.assertEqual(result.smoothing, Decimal('1'))
        # Four bins (two categories, unknown, missing), each receives +1:
        # adjusted baseline total is 2 + 4 = 6.
        first_row = result.table.rows[0]
        self.assertEqual(first_row['baseline_proportion'], Decimal('2') / Decimal('6'))

    def test_zero_smoothing_requires_no_empty_bins(self):
        with self.assertRaises(ValueError):
            PSIBaseline.from_categories('cat', ('a', 'b'), ('a', 'b', 'c'), smoothing=0)

        baseline = PSIBaseline.from_categories(
            'cat', ('a', 'b', 'z'), ('a', 'b'), unknown_label='z',
            smoothing=0, missing_policy=MISSING_EXCLUDE)
        full_batch = agate.Table((('a',), ('b',), ('z',)), ('cat',), (Text(),))
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            result = full_batch.aggregate(PSI(baseline))
        self.assertIsInstance(result.psi, Decimal)

        sparse_batch = agate.Table((('a',), ('b',)), ('cat',), (Text(),))
        with self.assertRaises(ValueError):
            sparse_batch.aggregate(PSI(baseline))
