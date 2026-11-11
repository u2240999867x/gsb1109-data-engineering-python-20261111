from agate.computations.base import Computation
from agate.data_types import Date, DateTime, Number
from agate.exceptions import DataTypeError
from agate.fiscal import FiscalDate


class FiscalDimension(Computation):
    """
    Map a :class:`.Date` or :class:`.DateTime` column onto a dimension of a
    4-4-5 style :class:`.FiscalCalendar`, such as the fiscal year, quarter,
    period or week-in-period.

    Apply one instance per dimension column, for example::

        calendar = FiscalCalendar(2, 1, weekday=6, rule='nearest')

        table.compute([
            ('fiscal_year', FiscalDimension('order_date', calendar, 'year')),
            ('fiscal_period', FiscalDimension('order_date', calendar, 'period'))
        ])

    :param column_name:
        The name of a column containing :class:`.Date` or
        :class:`.DateTime` values.
    :param calendar:
        A :class:`.FiscalCalendar` instance describing the fiscal year start
        rule, weeks per quarter and 53rd week placement.
    :param part:
        The fiscal dimension to compute. One of ``'year'``, ``'quarter'``,
        ``'period'``, ``'week_in_period'`` or ``'week_in_year'``.
    """
    def __init__(self, column_name, calendar, part='period'):
        if part not in FiscalDate._fields:
            raise ValueError('part must be one of: %s' % ', '.join(FiscalDate._fields))

        self._column_name = column_name
        self._calendar = calendar
        self._part = part

    def get_computed_data_type(self, table):
        return Number()

    def validate(self, table):
        column = table.columns[self._column_name]

        if not isinstance(column.data_type, (Date, DateTime)):
            raise DataTypeError('FiscalDimension column must contain Date or DateTime data.')

    def run(self, table):
        data_type = self.get_computed_data_type(table)
        new_column = []

        for row in table.rows:
            value = row[self._column_name]

            if value is None:
                new_column.append(None)
            else:
                fiscal_date = self._calendar.lookup(value)
                new_column.append(data_type.cast(getattr(fiscal_date, self._part)))

        return new_column
