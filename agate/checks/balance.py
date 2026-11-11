from decimal import Decimal

from agate.checks.check import Check


class Balance(Check):
    """
    Assert that an arithmetic balance holds within every group.

    The check passes for a group when::

        left_metrics - right_metrics == 0

    within an explicit :code:`tolerance`. Metrics are summed within each
    group before the balance is evaluated, so the relationship is checked at
    the level of the declared groups rather than on the grand totals. A
    mismatch that cancels out across groups (such as one region's sales
    recorded against another region) is therefore reported for both groups.

    Groups are taken as the union of the groups present across the tables.
    A group missing from one of the tables fails the check with the missing
    metric reported as :code:`None`; it is never implicitly treated as zero.

    :param left_metrics:
        The metric or sequence of metrics on the left hand side. Each metric
        is either a column name (when a single table is checked) or a
        :code:`(table_index, column_name)` tuple. Custom
        :code:`(name, target)` pairs may also be supplied; otherwise the
        column names are used as metric names.
    :param right_metrics:
        The metric or sequence of metrics on the right hand side, in the same
        format as :code:`left_metrics`.
    :param tolerance:
        Maximum absolute difference allowed. Must always be specified
        explicitly, even when an exact match is required (in which case pass
        :code:`Decimal(0)`). Negative values are rejected.
    :param name:
        A human-readable name for this check.
    """
    def __init__(self, left_metrics, right_metrics, tolerance, name=None):
        super().__init__(name)

        self._left_metrics = self._coerce_metrics(left_metrics, 'left_metrics')
        self._right_metrics = self._coerce_metrics(right_metrics, 'right_metrics')

        left_names = {name for name, _ in self._left_metrics}
        right_names = {name for name, _ in self._right_metrics}
        shared = left_names & right_names

        if shared:
            raise ValueError(f'left_metrics and right_metrics share metric names: {sorted(shared)}.')

        tolerance = Decimal(tolerance)

        if tolerance < 0:
            raise ValueError('tolerance must be non-negative.')

        self._tolerance_value = tolerance

    def metrics(self):
        return self._left_metrics + self._right_metrics

    def difference(self, values):
        left_total = sum((values[name] for name, _ in self._left_metrics), Decimal(0))
        right_total = sum((values[name] for name, _ in self._right_metrics), Decimal(0))

        return left_total - right_total

    @property
    def _tolerance(self):
        return self._tolerance_value

    def _coerce_metrics(self, metrics, argument_name):
        if isinstance(metrics, (str, tuple)) and not self._is_metric_pair(metrics):
            metrics = [metrics]

        coerced = []
        used_names = set()

        for metric in metrics:
            if self._is_metric_pair(metric):
                metric_name, target = metric
            elif isinstance(metric, str):
                metric_name, target = metric, metric
            else:
                metric_name, target = metric[1], metric

            if metric_name in used_names:
                raise ValueError(f'{argument_name} contains duplicate metric name "{metric_name}".')

            used_names.add(metric_name)
            coerced.append((metric_name, target))

        if not coerced:
            raise ValueError(f'{argument_name} must contain at least one metric.')

        return coerced

    @staticmethod
    def _is_metric_pair(metric):
        return (
            isinstance(metric, tuple)
            and len(metric) == 2
            and isinstance(metric[0], str)
            and (isinstance(metric[1], str) or isinstance(metric[1], tuple))
        )
