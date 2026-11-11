"""
Results returned by :class:`.Check` instances.
"""


class CheckResult:
    """
    The outcome of evaluating a :class:`.Check` against one or more
    :class:`.Table` instances.

    A result is truthy when every group satisfies the check and falsy when at
    least one group fails it.

    :param check:
        The :class:`.Check` that produced this result.
    :param failures:
        A sequence of :class:`.CheckFailure` instances describing the groups
        that did not satisfy the check.
    """
    def __init__(self, check, failures):
        self._check = check
        self._failures = tuple(failures)

    def __bool__(self):
        return not self._failures

    def __str__(self):
        if self:
            return f'{self._check.name}: passed'

        return f'{self._check.name}: {len(self._failures)} failing group(s)'

    def __repr__(self):
        return f'<agate.checks.CheckResult: {self}>'

    @property
    def check(self):
        """
        The :class:`.Check` that produced this result.
        """
        return self._check

    @property
    def passed(self):
        """
        :code:`True` if every group satisfied the check.
        """
        return not self._failures

    @property
    def failures(self):
        """
        A tuple of :class:`.CheckFailure` instances for the groups that did
        not satisfy the check. Groups that the check did not run on are never
        included.
        """
        return self._failures


class CheckFailure:
    """
    A single group that did not satisfy a :class:`.Check`.

    :param group_key:
        A tuple of grouping column values identifying the group, or
        :code:`()` for an ungrouped table.
    :param values:
        An :class:`collections.OrderedDict` of the metric values that
        participated in the check, keyed by metric name.
    :param difference:
        The signed amount by which the check failed, computed as the left
        hand side minus the right hand side of the asserted relationship.
    """
    def __init__(self, group_key, values, difference):
        self._group_key = group_key
        self._values = values
        self._difference = difference

    def __str__(self):
        return f'{self._group_key}: {dict(self._values)} (difference: {self._difference})'

    def __repr__(self):
        return f'<agate.checks.CheckFailure: {self}>'

    @property
    def group_key(self):
        """
        A tuple of grouping column values identifying the failing group.
        An empty tuple represents an ungrouped table.
        """
        return self._group_key

    @property
    def values(self):
        """
        An :class:`collections.OrderedDict` of the metric values that
        participated in the check, keyed by metric name. Missing groups are
        never fabricated, so a metric is absent rather than reported as zero
        when its table has no rows for this group.
        """
        return self._values

    @property
    def difference(self):
        """
        The signed amount by which the check failed, computed as the left
        hand side minus the right hand side of the asserted relationship.
        A negative value means the right hand side was larger.
        """
        return self._difference
