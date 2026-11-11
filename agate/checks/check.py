from collections import OrderedDict
from decimal import Decimal

from agate.checks.results import CheckFailure, CheckResult
from agate.data_types import Number
from agate.exceptions import DataTypeError


class Check:
    """
    Declarative reconciliation between metrics on one or more
    :class:`.Table` instances.

    Checks describe an arithmetic relationship between named metrics. Each
    metric is a column on a table. When a check is run with :meth:`.Table.run`
    the metrics are evaluated per group, using either the table's existing
    groups (a :class:`.TableSet`) or the whole table as a single group.

    Groups that are missing from a table are never fabricated. If a group
    exists on one table but not another, the missing metric is reported as
    :code:`None` and the group fails the check instead of being treated as
    zero. This means misassignments between groups (for example, a region's
    sales recorded against the wrong region) are caught even when the overall
    totals match.

    When implementing a custom check, override :meth:`Check.metrics`,
    :meth:`Check.difference` and optionally :meth:`Check.validate`.
    """
    def __init__(self, name=None):
        self._name = name or self.__class__.__name__

    def __str__(self):
        return self._name

    @property
    def name(self):
        """
        The human-readable name of this check.
        """
        return self._name

    def metrics(self):
        """
        Return the metrics that participate in this check as a sequence (or
        :class:`collections.OrderedDict`) of :code:`(name, target)` pairs.
        A target is either a column name when a single table is checked, or
        a :code:`(table_index, column_name)` tuple. Subclasses must override
        this.
        """
        raise NotImplementedError()

    def difference(self, values):
        """
        Given an :class:`collections.OrderedDict` of evaluated metric values,
        return the signed difference of the asserted relationship. The check
        passes when this value is within the configured tolerance.

        Subclasses must override this.
        """
        raise NotImplementedError()

    def validate(self, tables):
        """
        Validate that every metric column exists on its table and contains
        :class:`.Number` data. Called once before the check is run.
        """
        for table, column_names in self._columns_by_table(tables):
            for column_name in column_names:
                data_type = self._column_type(table, column_name)

                if not isinstance(data_type, Number):
                    raise DataTypeError(
                        f'{self._name} can only reconcile columns containing Number data, '
                        f'but "{column_name}" contains {type(data_type).__name__} data.'
                    )

    @staticmethod
    def _column_type(table, column_name):
        from agate.tableset import TableSet

        if isinstance(table, TableSet):
            index = table.column_names.index(column_name)
            return table.column_types[index]

        return table.columns[column_name].data_type

    def run(self, tables, group_by=None):
        """
        Evaluate this check against one or more tables.

        :param tables:
            A single :class:`.Table`/:class:`.TableSet`, or a sequence of
            them. A :class:`.TableSet` contributes one group per contained
            table; a :class:`.Table` is treated as a single ungrouped table.
        :param group_by:
            Optional grouping applied to every plain :class:`.Table`. May be
            a column name, a key function or a sequence of them for nested
            grouping. This does not fabricate groups that are absent from the
            data; use :meth:`.Table.homogenize` first if a complete set of
            groups is required.
        :returns:
            A :class:`.CheckResult` listing every failing group.
        """
        if not isinstance(tables, (list, tuple)):
            tables = [tables]

        if not tables:
            raise ValueError('At least one table is required to run a check.')

        groups = self._collect_groups(tables, group_by)

        grouped_flags = [is_grouped for _, is_grouped in groups]

        if any(grouped_flags) and not all(grouped_flags):
            raise ValueError(
                'All tables must be grouped or all must be ungrouped. Pass group_by for every plain '
                'Table, or provide TableSet instances for every table.'
            )

        self.validate(list(tables))

        metric_columns = self._metric_columns(tables)
        failures = []

        for group_key in self._group_keys(groups):
            values = OrderedDict()

            for metric_name, (table_index, column_name) in metric_columns:
                grouped_table, _ = groups[table_index]
                value = self._metric_value(grouped_table, group_key, column_name)
                values[metric_name] = value

            if any(value is None for value in values.values()):
                difference = None
            else:
                difference = self.difference(values)

            if difference is None or abs(difference) > self._tolerance:
                failures.append(CheckFailure(group_key, values, difference))

        return CheckResult(self, failures)

    @property
    def _tolerance(self):
        return Decimal(0)

    def _metric_columns(self, tables):
        metrics = self.metrics()

        if isinstance(metrics, OrderedDict):
            items = list(metrics.items())
        else:
            items = list(metrics)

        columns = []

        for metric_name, target in items:
            if isinstance(target, str):
                if len(tables) != 1:
                    raise ValueError(
                        f'Metric "{metric_name}" names a column but {len(tables)} tables were provided. '
                        'Pass a (table, column_name) tuple instead.'
                    )
                table_index = 0
                column_name = target
            else:
                table_index, column_name = target

                if type(table_index) is not int or table_index < 0 or table_index >= len(tables):
                    raise ValueError(
                        f'Metric "{metric_name}" references table index {table_index}, which does not exist.'
                    )

            columns.append((metric_name, (table_index, column_name)))

        return columns

    def _columns_by_table(self, tables):
        grouped = OrderedDict()

        for _, (table_index, column_name) in self._metric_columns(tables):
            grouped.setdefault(table_index, [])
            if column_name not in grouped[table_index]:
                grouped[table_index].append(column_name)

        return [(tables[index], column_names) for index, column_names in grouped.items()]

    def _collect_groups(self, tables, group_by):
        from agate.table import Table
        from agate.tableset import TableSet

        groups = []

        for table in tables:
            if isinstance(table, TableSet):
                groups.append((table, True))
            elif isinstance(table, Table):
                if not group_by:
                    groups.append((table, False))
                else:
                    keys = group_by if isinstance(group_by, (list, tuple)) else [group_by]
                    grouped = table

                    for key in keys:
                        grouped = grouped.group_by(key)

                    groups.append((grouped, True))
            else:
                raise ValueError('Checks can only be run on Table or TableSet instances.')

        return groups

    def _group_keys(self, groups):
        keys = []
        seen = set()

        for grouped_table, is_grouped in groups:
            if not is_grouped:
                key = ()
                if key not in seen:
                    seen.add(key)
                    keys.append(key)
                continue

            for group_key in self._iter_group_keys(grouped_table):
                if group_key not in seen:
                    seen.add(group_key)
                    keys.append(group_key)

        return keys

    def _iter_group_keys(self, grouped_table):
        from agate.tableset import TableSet

        for key, table in grouped_table.items():
            if isinstance(table, TableSet):
                for nested_key in self._iter_group_keys(table):
                    yield (key,) + nested_key
            else:
                yield (key,)

    def _metric_value(self, grouped_table, group_key, column_name):
        if group_key == ():
            table = grouped_table
        else:
            table = self._lookup_group(grouped_table, group_key)

            if table is None:
                return None

        column = table.columns[column_name]

        return sum(column.values_without_nulls(), Decimal(0))

    def _lookup_group(self, grouped_table, group_key):
        from agate.tableset import TableSet

        current = grouped_table

        for key in group_key:
            if not isinstance(current, TableSet) or key not in current.keys():
                return None

            current = current[key]

        return current
