import os
import tempfile
from decimal import Decimal

from agate import BinSpec
from agate.testcase import AgateTestCase


class TestBinSpec(AgateTestCase):
    def test_default_labels_closed_left(self):
        spec = BinSpec([0, 100, 500, 1000])

        self.assertEqual(spec.labels, ('[0 - 100)', '[100 - 500)', '[500 - 1,000]'))
        self.assertEqual(spec.closed, 'left')

    def test_default_labels_closed_right(self):
        spec = BinSpec([0, 100, 500, 1000], closed='right')

        self.assertEqual(spec.labels, ('[0 - 100]', '(100 - 500]', '(500 - 1,000]'))

    def test_edges_are_decimals(self):
        spec = BinSpec([0, 0.5, 1])

        self.assertEqual(spec.edges, (Decimal('0'), Decimal('0.5'), Decimal('1')))

    def test_requires_two_edges(self):
        with self.assertRaises(ValueError):
            BinSpec([100])

    def test_edges_must_increase(self):
        with self.assertRaises(ValueError):
            BinSpec([0, 100, 100])

        with self.assertRaises(ValueError):
            BinSpec([0, -1])

    def test_closed_must_be_valid(self):
        with self.assertRaises(ValueError):
            BinSpec([0, 100], closed='both')

    def test_label_count_must_match_bins(self):
        with self.assertRaises(ValueError):
            BinSpec([0, 100, 200], labels=['only one'])

    def test_labels_must_be_unique(self):
        with self.assertRaises(ValueError):
            BinSpec([0, 100, 200], labels=['same', 'same'])

        with self.assertRaises(ValueError):
            BinSpec([0, 100], labels=['underflow'])

    def test_locate_interior_boundary_closed_left(self):
        spec = BinSpec([0, 100, 200], closed='left')

        self.assertEqual(spec.locate(Decimal('100')), 1)
        self.assertEqual(spec.assign(Decimal('100')), '[100 - 200]')

    def test_locate_interior_boundary_closed_right(self):
        spec = BinSpec([0, 100, 200], closed='right')

        self.assertEqual(spec.locate(Decimal('100')), 0)
        self.assertEqual(spec.assign(Decimal('100')), '[0 - 100]')

    def test_locate_outer_edges_always_included(self):
        left = BinSpec([0, 100, 200], closed='left')
        right = BinSpec([0, 100, 200], closed='right')

        self.assertEqual(left.locate(Decimal('0')), 0)
        self.assertEqual(left.locate(Decimal('200')), 1)
        self.assertEqual(right.locate(Decimal('0')), 0)
        self.assertEqual(right.locate(Decimal('200')), 1)

    def test_locate_out_of_range_and_null(self):
        spec = BinSpec([0, 100])

        self.assertEqual(spec.locate(Decimal('-1')), BinSpec.UNDERFLOW)
        self.assertEqual(spec.locate(Decimal('101')), BinSpec.OVERFLOW)
        self.assertEqual(spec.locate(None), BinSpec.NULL)

        self.assertEqual(spec.assign(Decimal('-1')), 'underflow')
        self.assertEqual(spec.assign(Decimal('101')), 'overflow')
        self.assertIsNone(spec.assign(None))

    def test_custom_special_labels(self):
        spec = BinSpec(
            [0, 100],
            underflow_label='below zero',
            overflow_label='above max',
            null_label='missing'
        )

        self.assertEqual(spec.assign(Decimal('-1')), 'below zero')
        self.assertEqual(spec.assign(Decimal('101')), 'above max')
        self.assertEqual(spec.assign(None), 'missing')
        self.assertEqual(spec.bucket_labels(), ('below zero', '[0 - 100]', 'above max', 'missing'))

    def test_with_labels_does_not_change_assignment(self):
        spec = BinSpec([0, 100, 200], closed='left')
        renamed = spec.with_labels(['low', 'high'])

        self.assertEqual(renamed.labels, ('low', 'high'))

        for value in [None, Decimal('-1'), Decimal('0'), Decimal('50'), Decimal('100'), Decimal('200'), Decimal('500')]:
            self.assertEqual(renamed.locate(value), spec.locate(value))

        self.assertEqual(renamed.assign(Decimal('100')), 'high')

    def test_to_dict_from_dict_roundtrip(self):
        spec = BinSpec(
            [0, Decimal('0.5'), 100],
            labels=['small', 'big'],
            closed='right',
            underflow_label='u',
            overflow_label='o',
            null_label='n'
        )

        restored = BinSpec.from_dict(spec.to_dict())

        self.assertEqual(restored, spec)
        self.assertEqual(restored.edges, (Decimal('0'), Decimal('0.5'), Decimal('100')))
        self.assertEqual(restored.labels, ('small', 'big'))
        self.assertEqual(restored.closed, 'right')

    def test_to_json_from_json_roundtrip(self):
        spec = BinSpec([0, 100, 200])

        self.assertEqual(BinSpec.from_json(spec.to_json()), spec)

    def test_save_load_roundtrip(self):
        spec = BinSpec([0, 100, 200], closed='right')

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'bins.json')
            spec.save(path)
            loaded = BinSpec.load(path)

        self.assertEqual(loaded, spec)
        self.assertEqual(loaded.closed, 'right')
