import datetime

from agate import Table
from agate.data_types import Date, Number, Text
from agate.testcase import AgateTestCase


class TestHomogenizeCalendar(AgateTestCase):
    def setUp(self):
        self.rows = (
            ('A', datetime.date(2024, 1, 1), 10, '已观测'),
            ('A', datetime.date(2024, 1, 2), 0, '已观测'),
            ('A', datetime.date(2024, 1, 5), 7, '已观测'),
            ('C', datetime.date(2024, 1, 2), 5, '已观测')
        )

        self.text_type = Text()
        self.date_type = Date()
        self.number_type = Number()

        self.column_names = ['store', 'date', 'sales', 'status']
        self.column_types = [self.text_type, self.date_type, self.number_type, self.text_type]

        # Store A is open Mon/Tue/Thu/Fri (Wed 2024-01-03 is a suspension,
        # the weekend is not in its calendar). Store B has no observed rows.
        # Store C has no calendar entry at all.
        self.calendar = {
            'A': (
                datetime.date(2024, 1, 1),
                datetime.date(2024, 1, 7),
                [
                    datetime.date(2024, 1, 1),
                    datetime.date(2024, 1, 2),
                    datetime.date(2024, 1, 4),
                    datetime.date(2024, 1, 5)
                ]
            ),
            'B': (
                datetime.date(2024, 1, 1),
                datetime.date(2024, 1, 3),
                [
                    datetime.date(2024, 1, 2),
                    datetime.date(2024, 1, 3),
                    datetime.date(2024, 1, 10)
                ]
            )
        }

    def test_homogenize_calendar(self):
        table = Table(self.rows, self.column_names, self.column_types)

        def default_row(values):
            store, day = values
            return [store, day, None, '未观测']

        homogenized = table.homogenize_calendar('store', 'date', self.calendar, default_row)
        homogenized = homogenized.order_by(['store', 'date'])

        rows = (
            ('A', datetime.date(2024, 1, 1), 10, '已观测'),
            ('A', datetime.date(2024, 1, 2), 0, '已观测'),
            ('A', datetime.date(2024, 1, 4), None, '未观测'),
            ('A', datetime.date(2024, 1, 5), 7, '已观测'),
            ('B', datetime.date(2024, 1, 2), None, '未观测'),
            ('B', datetime.date(2024, 1, 3), None, '未观测'),
            ('C', datetime.date(2024, 1, 2), 5, '已观测')
        )

        homogenized.print_table()

        self.assertColumnNames(homogenized, self.column_names)
        self.assertColumnTypes(homogenized, [Text, Date, Number, Text])
        self.assertEqual(len(homogenized.rows), len(rows))
        self.assertRows(homogenized, rows)

    def test_homogenize_calendar_default_row_array(self):
        table = Table(self.rows, self.column_names, self.column_types)

        homogenized = table.homogenize_calendar('store', 'date', self.calendar, [None, '未观测'])
        homogenized = homogenized.order_by(['store', 'date'])

        rows = (
            ('A', datetime.date(2024, 1, 1), 10, '已观测'),
            ('A', datetime.date(2024, 1, 2), 0, '已观测'),
            ('A', datetime.date(2024, 1, 4), None, '未观测'),
            ('A', datetime.date(2024, 1, 5), 7, '已观测'),
            ('B', datetime.date(2024, 1, 2), None, '未观测'),
            ('B', datetime.date(2024, 1, 3), None, '未观测'),
            ('C', datetime.date(2024, 1, 2), 5, '已观测')
        )

        self.assertEqual(len(homogenized.rows), len(rows))
        self.assertRows(homogenized, rows)

    def test_homogenize_calendar_no_default_row(self):
        table = Table(self.rows, self.column_names, self.column_types)

        homogenized = table.homogenize_calendar('store', 'date', self.calendar)
        homogenized = homogenized.order_by(['store', 'date'])

        rows = (
            ('A', datetime.date(2024, 1, 1), 10, '已观测'),
            ('A', datetime.date(2024, 1, 2), 0, '已观测'),
            ('A', datetime.date(2024, 1, 4), None, None),
            ('A', datetime.date(2024, 1, 5), 7, '已观测'),
            ('B', datetime.date(2024, 1, 2), None, None),
            ('B', datetime.date(2024, 1, 3), None, None),
            ('C', datetime.date(2024, 1, 2), 5, '已观测')
        )

        self.assertEqual(len(homogenized.rows), len(rows))
        self.assertRows(homogenized, rows)

    def test_homogenize_calendar_unbounded_range(self):
        table = Table(self.rows, self.column_names, self.column_types)

        calendar = {
            'A': (
                None,
                None,
                [
                    datetime.date(2024, 1, 1),
                    datetime.date(2024, 1, 3)
                ]
            )
        }

        homogenized = table.homogenize_calendar('store', 'date', calendar)
        homogenized = homogenized.order_by(['store', 'date'])

        rows = (
            ('A', datetime.date(2024, 1, 1), 10, '已观测'),
            ('A', datetime.date(2024, 1, 2), 0, '已观测'),
            ('A', datetime.date(2024, 1, 3), None, None),
            ('A', datetime.date(2024, 1, 5), 7, '已观测'),
            ('C', datetime.date(2024, 1, 2), 5, '已观测')
        )

        self.assertEqual(len(homogenized.rows), len(rows))
        self.assertRows(homogenized, rows)

    def test_homogenize_calendar_empty_range(self):
        table = Table(self.rows, self.column_names, self.column_types)

        calendar = {
            'D': (
                datetime.date(2024, 1, 5),
                datetime.date(2024, 1, 3),
                [
                    datetime.date(2024, 1, 4)
                ]
            )
        }

        homogenized = table.homogenize_calendar('store', 'date', calendar)

        self.assertEqual(len(homogenized.rows), len(self.rows))
        self.assertRows(homogenized, self.rows)

    def test_homogenize_calendar_multiple_key_columns(self):
        rows = (
            ('X', 'A', datetime.date(2024, 1, 1), 10),
            ('X', 'A', datetime.date(2024, 1, 3), 0)
        )

        column_names = ['region', 'store', 'date', 'sales']
        column_types = [self.text_type, self.text_type, self.date_type, self.number_type]

        table = Table(rows, column_names, column_types)

        calendar = {
            ('X', 'A'): (
                datetime.date(2024, 1, 1),
                datetime.date(2024, 1, 3),
                [
                    datetime.date(2024, 1, 1),
                    datetime.date(2024, 1, 2),
                    datetime.date(2024, 1, 3)
                ]
            )
        }

        homogenized = table.homogenize_calendar(['region', 'store'], 'date', calendar, [None])
        homogenized = homogenized.order_by('date')

        expected = (
            ('X', 'A', datetime.date(2024, 1, 1), 10),
            ('X', 'A', datetime.date(2024, 1, 2), None),
            ('X', 'A', datetime.date(2024, 1, 3), 0)
        )

        self.assertColumnNames(homogenized, column_names)
        self.assertColumnTypes(homogenized, [Text, Text, Date, Number])
        self.assertEqual(len(homogenized.rows), len(expected))
        self.assertRows(homogenized, expected)
