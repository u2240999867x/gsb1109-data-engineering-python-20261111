from agate import utils
from agate.rows import Row


def homogenize_calendar(self, key, date_column, calendar, default_row=None):
    """
    Fill in missing rows in a grouped date series using a per-group business
    calendar.

    This can be used, for instance, to add rows for missing days in a daily
    sales report, while skipping days on which a given store was not open
    (weekends, holidays, suspensions) so they are not mistaken for missing
    reports.

    The :code:`calendar` maps each group key to a
    :code:`(start_date, end_date, business_days)` tuple. Rows are only
    generated for dates in :code:`business_days` that fall within the
    inclusive :code:`start_date` to :code:`end_date` range. Dates outside the
    range or not in :code:`business_days` are never filled in.
    :code:`start_date` and :code:`end_date` may be :code:`None` to leave the
    range unbounded on that side.

    Groups present in the :code:`calendar` but absent from the table are
    still processed, so a group with no observed rows at all gets a row for
    every business day in its valid range. Groups present in the table but
    absent from the :code:`calendar` are left untouched.

    Existing rows are never modified, so observed values such as zero sales
    are preserved as-is. Generated rows are created with the given
    :code:`default_row`, exactly as in :meth:`.Table.homogenize`, with the
    missing values being the group key values followed by the date. Use it
    to flag the new rows, e.g. with a status of "unobserved", and to keep
    their measure values distinct from observed zeros.

    :param key:
        Either a column name or a sequence of such names identifying the
        groups, e.g. the store.
    :param date_column:
        The name of the column containing the date of each row.
    :param calendar:
        A dictionary mapping each group key (a single value if :code:`key`
        is a single column name, or a sequence of values if it is a
        sequence of names) to a :code:`(start_date, end_date, business_days)`
        tuple, where :code:`business_days` is an iterable of dates that are
        valid business days for the group.
    :param default_row:
        An array of values or a function to generate new rows. If it is an
        array of values, its length should be equal to row length minus the
        number of key columns minus one for the date column. If it is a
        function, it should take an array of the missing values (group key
        values followed by the date) for each new row and output a full row
        including those values.
    :returns:
        A new :class:`.Table`.
    """
    rows = list(self._rows)

    if not utils.issequence(key):
        key = [key]

    full_key = list(key) + [date_column]

    column_values = [self._columns.get(name) for name in full_key]
    column_indexes = [self._column_names.index(name) for name in full_key]

    date_type = column_values[-1].data_type

    existing = set()
    for row in self._rows:
        existing.add(tuple(row[i] for i in column_indexes))

    differences = []
    for group_key, (start, end, business_days) in calendar.items():
        if not utils.issequence(group_key):
            group_key = (group_key,)

        group_key = tuple(column_values[i].data_type.cast(value) for i, value in enumerate(group_key))

        if start is not None:
            start = date_type.cast(start)
        if end is not None:
            end = date_type.cast(end)

        for day in set(business_days):
            day = date_type.cast(day)

            if start is not None and day < start:
                continue
            if end is not None and day > end:
                continue

            compare_value = group_key + (day,)

            if compare_value not in existing:
                differences.append(compare_value)

    for difference in differences:
        if callable(default_row):
            new_row = default_row(difference)
        else:
            if default_row is not None:
                new_row = list(default_row)
            else:
                new_row = [None] * (len(self._column_names) - len(full_key))

            for i, d in sorted(zip(column_indexes, difference)):
                new_row.insert(i, d)

        new_row = [self._columns[i].data_type.cast(v) for i, v in enumerate(new_row)]
        rows.append(Row(new_row, self._column_names))

    # Do not copy the row_names, since this function adds rows.
    return self._fork(rows, row_names=[])
