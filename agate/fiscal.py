"""
This module contains a reusable implementation of retail-style fiscal
calendars, such as 4-4-5, 4-5-4 and 5-4-4 week calendars. Unlike calendar
months, these calendars divide the fiscal year into whole weeks grouped into
periods, so reporting periods are always comparable week-over-week.
"""

import datetime
from collections import namedtuple
from zoneinfo import ZoneInfo

#: The fiscal placement of a single date.
FiscalDate = namedtuple('FiscalDate', [
    'year',
    'quarter',
    'period',
    'week_in_period',
    'week_in_year'
])

#: A single fiscal period in a generated period list.
FiscalPeriod = namedtuple('FiscalPeriod', [
    'year',
    'quarter',
    'period',
    'start',
    'end',
    'weeks'
])


class FiscalCalendar:
    """
    A reusable 4-4-5 style fiscal calendar.

    The fiscal year always starts on a configurable weekday near an anchor
    date, so every fiscal year is exactly 52 or 53 whole weeks. Days can never
    fall between two fiscal years or belong to two of them, because each year
    is defined as ``[year_start, next_year_start)``.

    :param start_month:
        The month of the anchor date used to locate the start of each fiscal
        year. For example ``2`` with ``start_day`` ``1`` and ``rule``
        ``'nearest'`` starts each fiscal year on the weekday nearest to
        February 1st.
    :param start_day:
        The day of the month of the anchor date.
    :param weekday:
        The weekday on which fiscal weeks (and thus fiscal years) begin, using
        the Python convention where Monday is ``0`` and Sunday is ``6``.
        Defaults to ``6`` (Sunday).
    :param rule:
        How the fiscal year start is derived from the anchor date. One of
        ``'nearest'`` (the closest occurrence of ``weekday`` to the anchor),
        ``'on_or_before'`` (the last occurrence of ``weekday`` on or before
        the anchor) or ``'on_or_after'`` (the first occurrence of ``weekday``
        on or after the anchor).
    :param weeks_per_quarter:
        A sequence of week counts per period within each quarter, e.g.
        ``(4, 4, 5)``. The pattern is repeated for each of the four quarters
        and must sum to 13 weeks (52 weeks per year).
    :param extra_week_period:
        The 1-based period number to which the 53rd week is assigned in fiscal
        years that contain 53 weeks. Defaults to the last period of the year.
    :param timezone:
        The business timezone used to derive dates from timestamps. May be a
        ``tzinfo`` instance or an IANA timezone name such as
        ``'America/New_York'``. Aware datetimes are converted with
        :meth:`datetime.datetime.astimezone` and epoch timestamps with
        :meth:`datetime.datetime.fromtimestamp` using this timezone, never the
        machine-local timezone. Naive datetimes are assumed to already be in
        the business timezone.
    """
    RULES = ('nearest', 'on_or_before', 'on_or_after')

    QUARTERS_PER_YEAR = 4

    WEEKS_PER_YEAR = 52

    def __init__(self, start_month, start_day, weekday=6, rule='nearest',
                 weeks_per_quarter=(4, 4, 5), extra_week_period=None,
                 timezone=None):
        if rule not in self.RULES:
            raise ValueError('rule must be one of: %s' % ', '.join(self.RULES))

        if not 0 <= weekday <= 6:
            raise ValueError('weekday must be between 0 (Monday) and 6 (Sunday).')

        weeks_per_quarter = tuple(weeks_per_quarter)

        if not weeks_per_quarter or any(not isinstance(w, int) or w < 1 for w in weeks_per_quarter):
            raise ValueError('weeks_per_quarter must be a sequence of positive integers.')

        if sum(weeks_per_quarter) * self.QUARTERS_PER_YEAR != self.WEEKS_PER_YEAR:
            raise ValueError('weeks_per_quarter must sum to 13 weeks (52 weeks per fiscal year).')

        periods_per_year = len(weeks_per_quarter) * self.QUARTERS_PER_YEAR

        if extra_week_period is None:
            extra_week_period = periods_per_year

        if not 1 <= extra_week_period <= periods_per_year:
            raise ValueError('extra_week_period must be between 1 and %i.' % periods_per_year)

        if isinstance(timezone, str):
            timezone = ZoneInfo(timezone)

        # Validate the anchor date is a real date.
        datetime.date(2000, start_month, start_day)

        self._start_month = start_month
        self._start_day = start_day
        self._weekday = weekday
        self._rule = rule
        self._weeks_per_quarter = weeks_per_quarter
        self._extra_week_period = extra_week_period
        self._timezone = timezone

    def _year_start(self, calendar_year):
        """
        The first day of the fiscal year anchored in the given calendar year.
        """
        anchor = datetime.date(calendar_year, self._start_month, self._start_day)
        days_back = (anchor.weekday() - self._weekday) % 7

        if self._rule == 'on_or_before':
            return anchor - datetime.timedelta(days=days_back)

        if self._rule == 'on_or_after':
            return anchor + datetime.timedelta(days=(7 - days_back) % 7)

        before = anchor - datetime.timedelta(days=days_back)
        after = before + datetime.timedelta(days=7)

        if (anchor - before) <= (after - anchor):
            return before

        return after

    def _fiscal_year(self, day):
        """
        The fiscal year containing the given date.
        """
        year = day.year

        if day >= self._year_start(year + 1):
            return year + 1

        if day < self._year_start(year):
            return year - 1

        return year

    def _period_layout(self, total_weeks):
        """
        A list of ``(first_week_index, weeks)`` pairs, one per period.
        """
        layout = []
        week_index = 0

        for period in range(len(self._weeks_per_quarter) * self.QUARTERS_PER_YEAR):
            weeks = self._weeks_per_quarter[period % len(self._weeks_per_quarter)]

            if total_weeks > self.WEEKS_PER_YEAR and period + 1 == self._extra_week_period:
                weeks += total_weeks - self.WEEKS_PER_YEAR

            layout.append((week_index, weeks))
            week_index += weeks

        return layout

    def _to_date(self, value):
        """
        Coerce a date, datetime or epoch timestamp to a date in the business
        timezone.
        """
        if isinstance(value, datetime.datetime):
            if value.tzinfo is not None and self._timezone is not None:
                value = value.astimezone(self._timezone)

            return value.date()

        if isinstance(value, datetime.date):
            return value

        if isinstance(value, (int, float)):
            if self._timezone is None:
                raise ValueError('A timezone is required to convert epoch timestamps to dates.')

            return datetime.datetime.fromtimestamp(value, tz=self._timezone).date()

        raise TypeError('Value must be a date, datetime or epoch timestamp.')

    def lookup(self, value):
        """
        Locate a date, datetime or epoch timestamp in the fiscal calendar.

        :param value:
            A :class:`datetime.date`, :class:`datetime.datetime` or epoch
            timestamp. Timestamps and aware datetimes are first converted to
            the business timezone.
        :returns:
            A :class:`FiscalDate` with the fiscal ``year``, ``quarter``,
            ``period``, ``week_in_period`` and ``week_in_year``.
        """
        day = self._to_date(value)
        year = self._fiscal_year(day)
        year_start = self._year_start(year)
        next_year_start = self._year_start(year + 1)
        total_weeks = (next_year_start - year_start).days // 7
        week_index = (day - year_start).days // 7

        periods_per_quarter = len(self._weeks_per_quarter)

        for period, (first_week, weeks) in enumerate(self._period_layout(total_weeks), start=1):
            if week_index < first_week + weeks:
                return FiscalDate(
                    year=year,
                    quarter=(period - 1) // periods_per_quarter + 1,
                    period=period,
                    week_in_period=week_index - first_week + 1,
                    week_in_year=week_index + 1
                )

        raise ValueError('%s is not within fiscal year %i.' % (day, year))  # pragma: no cover

    def periods(self, start, end):
        """
        Generate the complete list of fiscal periods overlapping a date range.

        :param start:
            The first date of the range, as a date, datetime or timestamp.
        :param end:
            The last date of the range, as a date, datetime or timestamp.
        :returns:
            A list of :class:`FiscalPeriod` tuples, each with the fiscal
            ``year``, ``quarter``, ``period``, first day (``start``), last
            day (``end``, inclusive) and number of ``weeks``. Adjacent
            periods are contiguous: each period starts the day after the
            previous one ends.
        """
        start = self._to_date(start)
        end = self._to_date(end)

        if end < start:
            raise ValueError('end must not be before start.')

        year = self._fiscal_year(start)
        results = []
        periods_per_quarter = len(self._weeks_per_quarter)

        while True:
            year_start = self._year_start(year)
            total_weeks = (self._year_start(year + 1) - year_start).days // 7

            for period, (first_week, weeks) in enumerate(self._period_layout(total_weeks), start=1):
                period_start = year_start + datetime.timedelta(weeks=first_week)
                period_end = period_start + datetime.timedelta(weeks=weeks, days=-1)

                if period_start > end:
                    return results

                if period_end < start:
                    continue

                results.append(FiscalPeriod(
                    year=year,
                    quarter=(period - 1) // periods_per_quarter + 1,
                    period=period,
                    start=period_start,
                    end=period_end,
                    weeks=weeks
                ))

            year += 1
