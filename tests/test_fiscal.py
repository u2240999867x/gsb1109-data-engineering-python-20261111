import datetime
import unittest
from decimal import Decimal
from zoneinfo import ZoneInfo

from agate import Table
from agate.computations import FiscalDimension
from agate.data_types import Date, DateTime, Number, Text
from agate.exceptions import DataTypeError
from agate.fiscal import FiscalCalendar, FiscalDate, FiscalPeriod


class TestFiscalCalendar(unittest.TestCase):
    def setUp(self):
        # NRF-style 4-4-5: fiscal year starts on the Sunday nearest Feb 1
        self.calendar = FiscalCalendar(2, 1, weekday=6, rule='nearest')

    def test_year_start_rules(self):
        nearest = FiscalCalendar(2, 1, weekday=6, rule='nearest')
        on_or_before = FiscalCalendar(2, 1, weekday=6, rule='on_or_before')
        on_or_after = FiscalCalendar(2, 1, weekday=6, rule='on_or_after')

        # 2024-02-01 is a Thursday; nearest Sunday is 2024-02-04
        self.assertEqual(nearest._year_start(2024), datetime.date(2024, 2, 4))
        self.assertEqual(on_or_before._year_start(2024), datetime.date(2024, 1, 28))
        self.assertEqual(on_or_after._year_start(2024), datetime.date(2024, 2, 4))

        # 2021-02-01 is a Monday; nearest Sunday is 2021-01-31
        self.assertEqual(nearest._year_start(2021), datetime.date(2021, 1, 31))

    def test_lookup(self):
        fiscal_date = self.calendar.lookup(datetime.date(2024, 2, 4))

        self.assertEqual(fiscal_date, FiscalDate(2024, 1, 1, 1, 1))
        self.assertEqual(fiscal_date.year, 2024)
        self.assertEqual(fiscal_date.quarter, 1)
        self.assertEqual(fiscal_date.period, 1)
        self.assertEqual(fiscal_date.week_in_period, 1)
        self.assertEqual(fiscal_date.week_in_year, 1)

        # Week 5 of the year is the first week of period 2 (4-4-5)
        self.assertEqual(
            self.calendar.lookup(datetime.date(2024, 3, 3)),
            FiscalDate(2024, 1, 2, 1, 5)
        )

        # Quarter 2 starts with period 4
        self.assertEqual(
            self.calendar.lookup(datetime.date(2024, 5, 5)),
            FiscalDate(2024, 2, 4, 1, 14)
        )

    def test_lookup_year_boundary(self):
        # FY2023 ends 2024-02-03; FY2024 starts 2024-02-04
        self.assertEqual(self.calendar.lookup(datetime.date(2024, 2, 3)).year, 2023)
        self.assertEqual(self.calendar.lookup(datetime.date(2024, 2, 4)).year, 2024)

        # The fiscal year can also begin in late December of the prior
        # calendar year: the Sunday nearest 2024-01-01 is 2023-12-31
        calendar = FiscalCalendar(1, 1, weekday=6, rule='nearest')
        self.assertEqual(calendar._year_start(2024), datetime.date(2023, 12, 31))
        self.assertEqual(calendar.lookup(datetime.date(2023, 12, 31)).year, 2024)
        self.assertEqual(calendar.lookup(datetime.date(2023, 12, 30)).year, 2023)

    def test_lookup_53_week_year(self):
        # FY2023 has 53 weeks; the extra week goes to the last period
        year_start = self.calendar._year_start(2023)
        next_year_start = self.calendar._year_start(2024)
        self.assertEqual((next_year_start - year_start).days // 7, 53)

        self.assertEqual(
            self.calendar.lookup(datetime.date(2024, 2, 3)),
            FiscalDate(2023, 4, 12, 6, 53)
        )

    def test_lookup_53_week_year_custom_period(self):
        calendar = FiscalCalendar(2, 1, weekday=6, rule='nearest', extra_week_period=1)

        # The extra week becomes week 5 of period 1
        self.assertEqual(
            calendar.lookup(datetime.date(2023, 2, 26)),
            FiscalDate(2023, 1, 1, 5, 5)
        )
        # And period 2 starts a week later than in a 52-week year
        self.assertEqual(
            calendar.lookup(datetime.date(2023, 3, 5)),
            FiscalDate(2023, 1, 2, 1, 6)
        )

    def test_no_gaps_or_double_assignment(self):
        day = datetime.date(2015, 1, 1)
        end = datetime.date(2030, 12, 31)

        previous = None

        while day <= end:
            fiscal_date = self.calendar.lookup(day)

            if previous is not None:
                if fiscal_date.period == previous.period:
                    # Same period: week advances only when the week does
                    self.assertIn(
                        fiscal_date.week_in_period - previous.week_in_period,
                        (0, 1)
                    )
                else:
                    # New period always starts at week 1
                    self.assertEqual(fiscal_date.week_in_period, 1)

                    if fiscal_date.year == previous.year:
                        self.assertEqual(fiscal_date.period, previous.period + 1)
                    else:
                        self.assertEqual(fiscal_date.year, previous.year + 1)
                        self.assertEqual(fiscal_date.period, 1)
                        self.assertEqual(fiscal_date.week_in_year, 1)

            previous = fiscal_date
            day += datetime.timedelta(days=1)

    def test_periods(self):
        periods = self.calendar.periods(datetime.date(2022, 6, 1), datetime.date(2024, 6, 1))

        self.assertEqual(len(periods), 24)
        self.assertEqual(
            periods[0],
            FiscalPeriod(2022, 2, 5, datetime.date(2022, 5, 29), datetime.date(2022, 6, 25), 4)
        )
        self.assertEqual(periods[-1].year, 2024)
        self.assertEqual(periods[-1].period, 4)

        # Periods are contiguous and non-overlapping
        for earlier, later in zip(periods, periods[1:]):
            self.assertEqual(later.start, earlier.end + datetime.timedelta(days=1))
            self.assertEqual((earlier.end - earlier.start).days + 1, earlier.weeks * 7)

        # The 53-week fiscal year has a 6-week final period
        final_2023 = [p for p in periods if p.year == 2023 and p.period == 12][0]
        self.assertEqual(final_2023.weeks, 6)
        self.assertEqual(sum(p.weeks for p in periods if p.year == 2023), 53)

    def test_periods_cover_every_day(self):
        periods = self.calendar.periods(datetime.date(2020, 1, 1), datetime.date(2025, 12, 31))

        day = periods[0].start

        while day <= periods[-1].end:
            containing = [p for p in periods if p.start <= day <= p.end]
            self.assertEqual(len(containing), 1)
            day += datetime.timedelta(days=1)

    def test_periods_invalid_range(self):
        with self.assertRaises(ValueError):
            self.calendar.periods(datetime.date(2024, 6, 1), datetime.date(2024, 1, 1))

    def test_timezone_aware_datetime(self):
        calendar = FiscalCalendar(2, 1, weekday=6, rule='nearest', timezone='America/New_York')

        # 2024-02-04 02:00 UTC is still 2024-02-03 in New York (FY2023)
        value = datetime.datetime(2024, 2, 4, 2, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(calendar.lookup(value).year, 2023)
        self.assertEqual(
            calendar.lookup(value),
            FiscalDate(2023, 4, 12, 6, 53)
        )

        # Same instant with the business timezone set to UTC is FY2024
        utc_calendar = FiscalCalendar(2, 1, weekday=6, rule='nearest', timezone=ZoneInfo('UTC'))
        self.assertEqual(utc_calendar.lookup(value).year, 2024)

    def test_timezone_epoch_timestamp(self):
        calendar = FiscalCalendar(2, 1, weekday=6, rule='nearest', timezone='America/New_York')

        # 2024-02-04 02:00 UTC as an epoch timestamp
        timestamp = datetime.datetime(2024, 2, 4, 2, 0, tzinfo=datetime.timezone.utc).timestamp()
        self.assertEqual(calendar.lookup(timestamp).year, 2023)

    def test_epoch_timestamp_requires_timezone(self):
        with self.assertRaises(ValueError):
            self.calendar.lookup(1707000000)

    def test_naive_datetime_is_business_local(self):
        calendar = FiscalCalendar(2, 1, weekday=6, rule='nearest', timezone='America/New_York')

        value = datetime.datetime(2024, 2, 4, 2, 0)
        self.assertEqual(calendar.lookup(value).year, 2024)

    def test_lookup_invalid_type(self):
        with self.assertRaises(TypeError):
            self.calendar.lookup('2024-02-04')

    def test_invalid_arguments(self):
        with self.assertRaises(ValueError):
            FiscalCalendar(2, 1, rule='sometimes')

        with self.assertRaises(ValueError):
            FiscalCalendar(2, 1, weekday=7)

        with self.assertRaises(ValueError):
            FiscalCalendar(2, 1, weeks_per_quarter=(4, 4, 4))

        with self.assertRaises(ValueError):
            FiscalCalendar(2, 1, weeks_per_quarter=(4, 0, 5))

        with self.assertRaises(ValueError):
            FiscalCalendar(2, 1, extra_week_period=13)

        with self.assertRaises(ValueError):
            FiscalCalendar(2, 30)


class TestFiscalDimension(unittest.TestCase):
    def setUp(self):
        self.calendar = FiscalCalendar(2, 1, weekday=6, rule='nearest')

        self.rows = (
            (datetime.date(2024, 2, 3),),
            (datetime.date(2024, 2, 4),),
            (datetime.date(2024, 3, 3),),
            (None,)
        )

        self.table = Table(self.rows, ['date'], [Date()])

    def test_compute_dimensions(self):
        table = self.table.compute([
            ('fiscal_year', FiscalDimension('date', self.calendar, 'year')),
            ('fiscal_quarter', FiscalDimension('date', self.calendar, 'quarter')),
            ('fiscal_period', FiscalDimension('date', self.calendar, 'period')),
            ('fiscal_week', FiscalDimension('date', self.calendar, 'week_in_period'))
        ])

        self.assertEqual(table.column_names, (
            'date', 'fiscal_year', 'fiscal_quarter', 'fiscal_period', 'fiscal_week'
        ))

        self.assertIsInstance(table.columns['fiscal_year'].data_type, Number)

        self.assertSequenceEqual(table.columns['fiscal_year'], (Decimal('2023'), Decimal('2024'), Decimal('2024'), None))
        self.assertSequenceEqual(table.columns['fiscal_quarter'], (Decimal('4'), Decimal('1'), Decimal('1'), None))
        self.assertSequenceEqual(table.columns['fiscal_period'], (Decimal('12'), Decimal('1'), Decimal('2'), None))
        self.assertSequenceEqual(table.columns['fiscal_week'], (Decimal('6'), Decimal('1'), Decimal('1'), None))

    def test_compute_datetime_column(self):
        rows = (
            (datetime.datetime(2024, 2, 4, 10, 30),),
        )

        table = Table(rows, ['ts'], [DateTime()])
        table = table.compute([
            ('fiscal_year', FiscalDimension('ts', self.calendar, 'year'))
        ])

        self.assertSequenceEqual(table.columns['fiscal_year'], (Decimal('2024'),))

    def test_invalid_part(self):
        with self.assertRaises(ValueError):
            FiscalDimension('date', self.calendar, 'month')

    def test_invalid_column_type(self):
        table = Table((('a',),), ['name'], [Text()])

        with self.assertRaises(DataTypeError):
            table.compute([
                ('fiscal_year', FiscalDimension('name', self.calendar, 'year'))
            ])


if __name__ == '__main__':
    unittest.main()
