"""
Pre-flight checks for whether a batch of values can be cast to a target
:class:`.DataType` before a :class:`.Table` is created.

Unlike :class:`.TypeTester`, which guesses a type on the caller's behalf,
:class:`.TypeChecker` only reports what would happen if a given target type
were applied. Each value is classified as one of:

1. :data:`EXACT` -- the value casts and its representation is fully preserved.
2. :data:`LOSSY` -- the value casts, but its representation changes.
3. :data:`INVALID` -- the value can not be cast at all.

The original values are always retained alongside their row position, so the
caller can confirm the report and reuse the checked batch without re-reading
the source data.
"""

from collections import namedtuple

from agate.data_types.base import DataType
from agate.exceptions import CastError

#: The value casts to the target type with its representation fully preserved.
EXACT = 'exact'

#: The value casts to the target type, but its representation changes.
LOSSY = 'lossy'

#: The value can not be cast to the target type.
INVALID = 'invalid'


class TypeChecker:
    """
    Check whether a batch of values can be cast to a target
    :class:`.DataType` without committing to that type.

    A value is classified as :data:`EXACT` when it casts without a
    :class:`.CastError` and rendering the cast value through the type's
    :meth:`.DataType.csvify` method reproduces the original input. It is
    classified as :data:`LOSSY` when the cast succeeds but the round-trip
    representation differs (for example, ``"007"`` cast to :class:`.Number`
    becomes ``"7"``), and :data:`INVALID` when the cast fails.

    The input is always materialized, so a single-use iterator may be passed
    directly and the checked values remain available afterwards via
    :attr:`.TypeCheckResult.values`.

    :param data_type:
        The :class:`.DataType` instance every value is checked against.
    """

    def __init__(self, data_type):
        if not isinstance(data_type, DataType):
            raise ValueError('data_type must be an instance of DataType.')

        self._data_type = data_type

    def run(self, values):
        """
        Check each value against the target :class:`.DataType`.

        :param values:
            Any iterable of raw values, including a single-use iterator.
        :returns:
            A :class:`.TypeCheckResult` retaining the materialized values and
            a per-value report with row positions and original inputs.
        """
        values = tuple(values)

        report = []

        for row, value in enumerate(values):
            try:
                casted = self._data_type.cast(value)
            except CastError:
                report.append(TypeCheckIssue(INVALID, row, value, None))
                continue

            if value is None or casted is None or self._preserves(value, casted):
                report.append(TypeCheckIssue(EXACT, row, value, casted))
            else:
                report.append(TypeCheckIssue(LOSSY, row, value, casted))

        return TypeCheckResult(self._data_type, values, tuple(report))

    def _preserves(self, value, casted):
        if casted is None:
            normalized = value.strip() if isinstance(value, str) else value
            return isinstance(normalized, str) and normalized.lower() in self._data_type.null_values

        try:
            rendered = self._data_type.csvify(casted)
        except Exception:
            return False

        if rendered is None:
            return False

        original = value.strip() if isinstance(value, str) else str(value)

        return str(rendered) == original


class TypeCheckResult(namedtuple('TypeCheckResult', ['data_type', 'values', 'report'])):
    """
    The outcome of a :class:`.TypeChecker` run.

    :param data_type:
        The :class:`.DataType` the values were checked against.
    :param values:
        The materialized batch of original values, in their original order.
    :param report:
        A tuple of :class:`.TypeCheckIssue` instances, one per value in row
        order, each tagged with :data:`EXACT`, :data:`LOSSY` or
        :data:`INVALID`.
    """

    __slots__ = ()

    @property
    def exact(self):
        """
        The :class:`.TypeCheckIssue` instances classified as :data:`EXACT`.
        """
        return tuple(issue for issue in self.report if issue.status == EXACT)

    @property
    def lossy(self):
        """
        The :class:`.TypeCheckIssue` instances classified as :data:`LOSSY`.
        """
        return tuple(issue for issue in self.report if issue.status == LOSSY)

    @property
    def invalid(self):
        """
        The :class:`.TypeCheckIssue` instances classified as :data:`INVALID`.
        """
        return tuple(issue for issue in self.report if issue.status == INVALID)

    @property
    def valid(self):
        """
        Whether every value can be cast, including values that would lose
        representation.
        """
        return not self.invalid

    @property
    def exact_match(self):
        """
        Whether every value casts with its representation fully preserved.
        """
        return not self.lossy and not self.invalid

    def issue_at(self, row):
        """
        Return the :class:`.TypeCheckIssue` recorded for ``row``.
        """
        return self.report[row]


class TypeCheckIssue(namedtuple('TypeCheckIssue', ['status', 'row', 'value', 'casted_value'])):
    """
    The classification of a single checked value.

    :param status:
        One of :data:`EXACT`, :data:`LOSSY` or :data:`INVALID`.
    :param row:
        The zero-based position of the value within the checked batch.
    :param value:
        The original input value.
    :param casted_value:
        The cast value, or :code:`None` when the status is :data:`INVALID`.
    """

    __slots__ = ()
