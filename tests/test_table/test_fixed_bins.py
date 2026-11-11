from decimal import Decimal

from agate import BinSpec, Table
from agate.data_types import Number, Text
from agate.tableset import TableSet
from agate.testcase import AgateTestCase


class TestFixedBins(AgateTestCase):
    def setUp(self):
        self.number_type = Number()
        self.column_names = ['amount']
        self.column_types = [self.number_type]
        self.spec = BinSpec([0, 100, 500, 1000])

    def make_table(self, values):
        return Table([[v] for v in values], self.column_names, self.column_types)

    def test_counts_cover_all_buckets(self):
        table = self.make_table([
            Decimal('-5'),
            Decimal('0'),
            Decimal('100'),
            Decimal('250'),
            Decimal('500'),
            Decimal('999'),
            Decimal('1000'),
            Decimal('1500'),
            None
        ])

        counts = table.fixed_bins('amount', self.spec).counts

        self.assertColumnNames(counts, ['amount', 'Count'])
        self.assertColumnTypes(counts, [Text, Number])

        self.assertSequenceEqual(counts.rows[0], ['underflow', 1])
        self.assertSequenceEqual(counts.rows[1], ['[0 - 100)', 1])
        self.assertSequenceEqual(counts.rows[2], ['[100 - 500)', 2])
        self.assertSequenceEqual(counts.rows[3], ['[500 - 1,000]', 3])
        self.assertSequenceEqual(counts.rows[4], ['overflow', 1])
        self.assertSequenceEqual(counts.rows[5], [None, 1])

    def test_empty_bins_are_kept(self):
        table = self.make_table([Decimal('10'), Decimal('20')])

        result = table.fixed_bins('amount', self.spec)

        self.assertSequenceEqual(
            [row['Count'] for row in result.counts.rows],
            [0, 2, 0, 0, 0, 0]
        )

        for label in self.spec.bucket_labels():
            self.assertIn(label, result.tables.keys())

        self.assertEqual(len(result.tables['[100 - 500)'].rows), 0)
        self.assertEqual(len(result.tables['underflow'].rows), 0)

    def test_records_are_grouped_per_bin(self):
        table = self.make_table([Decimal('10'), Decimal('250'), Decimal('20'), None])

        result = table.fixed_bins('amount', self.spec)

        self.assertIsInstance(result.tables, TableSet)
        self.assertSequenceEqual(
            [row['amount'] for row in result.tables['[0 - 100)'].rows],
            [Decimal('10'), Decimal('20')]
        )
        self.assertSequenceEqual(
            [row['amount'] for row in result.tables['[100 - 500)'].rows],
            [Decimal('250')]
        )
        self.assertSequenceEqual(
            [row['amount'] for row in result.tables[None].rows],
            [None]
        )

    def test_same_spec_applies_to_different_tables(self):
        table_one = self.make_table([Decimal('10'), Decimal('2000')])
        table_two = self.make_table([Decimal('600'), Decimal('700')])

        counts_one = table_one.fixed_bins('amount', self.spec).counts
        counts_two = table_two.fixed_bins('amount', self.spec).counts

        self.assertSequenceEqual(
            [row['amount'] for row in counts_one.rows],
            [row['amount'] for row in counts_two.rows]
        )
        self.assertSequenceEqual(
            [row['Count'] for row in counts_one.rows],
            [0, 1, 0, 0, 1, 0]
        )
        self.assertSequenceEqual(
            [row['Count'] for row in counts_two.rows],
            [0, 0, 0, 2, 0, 0]
        )

    def test_closed_right_boundary_assignment(self):
        spec = BinSpec([0, 100, 200], closed='right')
        table = self.make_table([Decimal('0'), Decimal('100'), Decimal('200')])

        counts = table.fixed_bins('amount', spec).counts

        self.assertSequenceEqual(counts.rows[1], ['[0 - 100]', 2])
        self.assertSequenceEqual(counts.rows[2], ['(100 - 200]', 1])

    def test_renamed_labels_do_not_change_membership(self):
        renamed = self.spec.with_labels(['low', 'mid', 'high'])
        table = self.make_table([Decimal('100'), Decimal('250')])

        original = table.fixed_bins('amount', self.spec)
        relabeled = table.fixed_bins('amount', renamed)

        self.assertSequenceEqual(
            [row['Count'] for row in original.counts.rows],
            [row['Count'] for row in relabeled.counts.rows]
        )
        self.assertSequenceEqual(
            [row['amount'] for row in relabeled.tables['mid'].rows],
            [Decimal('100'), Decimal('250')]
        )

    def test_loaded_spec_produces_same_counts(self):
        table = self.make_table([Decimal('100'), Decimal('-1'), None])

        restored = BinSpec.from_json(self.spec.to_json())

        self.assertSequenceEqual(
            [tuple(row.values()) for row in table.fixed_bins('amount', self.spec).counts.rows],
            [tuple(row.values()) for row in table.fixed_bins('amount', restored).counts.rows]
        )
