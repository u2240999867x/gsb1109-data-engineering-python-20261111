import math
import warnings
from collections import OrderedDict
from decimal import Decimal

from agate.aggregations.base import Aggregation
from agate.data_types import Number, Text
from agate.exceptions import DataTypeError
from agate.warns import PSIEmptyBatchWarning, PSINewCategoryWarning

#: Default smoothing count added to every bin before proportions are derived.
#: It keeps zero-frequency bins mathematically defined and makes the smoothing
#: policy explicit instead of silently discarding bins.
DEFAULT_SMOOTHING = Decimal('0.5')

#: Null values are counted in a dedicated, separately labeled bin.
MISSING_SEPARATE = 'separate'

#: Null values are excluded from both baseline and batch counts.
MISSING_EXCLUDE = 'exclude'

#: Null values are counted together with other unrecognized values in the
#: unknown bin.
MISSING_UNKNOWN = 'unknown'

MISSING_POLICIES = (MISSING_SEPARATE, MISSING_EXCLUDE, MISSING_UNKNOWN)

#: Label of the bin that reserves capacity for values not covered by the
#: declared categories or numeric boundaries.
DEFAULT_UNKNOWN_LABEL = '__unknown__'

#: Result status when a PSI value was calculated.
PSI_STATUS_OK = 'ok'

#: Result status when the batch contains no usable observations, so no PSI
#: value can be calculated. This is an inconclusive state, not evidence that
#: no drift exists.
PSI_STATUS_EMPTY_BATCH = 'empty_batch'

#: Internal sentinel for a value excluded from all bins by the missing policy.
_EXCLUDE = object()


def _as_decimal(value, name):
    if isinstance(value, Decimal):
        return value

    try:
        return Decimal(str(value))
    except Exception as exc:
        raise ValueError('{} must be numeric.'.format(name)) from exc


def _log(value):
    """
    Natural logarithm in :class:`decimal.Decimal` arithmetic, falling back to
    :mod:`math` on Python versions without :meth:`decimal.Decimal.ln`.
    """
    try:
        return value.ln()
    except AttributeError:  # pragma: no cover
        return Decimal(repr(math.log(float(value))))


class PSIBaseline:
    """
    An immutable PSI baseline: a fixed column name, bin declaration, baseline
    counts and the policies applied to every later batch.

    Instances are created with :meth:`from_categories` (categorical columns)
    or :meth:`from_bins` (numeric columns with explicit boundaries) and then
    reused, unchanged, in :class:`PSI` for each daily batch. The bin order,
    labels, smoothing parameter and missing-value policy are frozen here so
    that batches can never silently re-bin the data.

    Use :attr:`config` to record the exact declaration alongside monitoring
    results.
    """
    def __init__(self, column_name, kind, bin_keys, bin_labels, counts, boundaries,
                 categories, smoothing, missing_policy, unknown_label):
        self._column_name = column_name
        self._kind = kind
        self._bin_keys = tuple(bin_keys)
        self._bin_labels = tuple(bin_labels)
        self._counts = OrderedDict((key, counts[key]) for key in bin_keys)
        self._boundaries = boundaries
        self._categories = categories
        self._smoothing = smoothing
        self._missing_policy = missing_policy
        self._unknown_label = unknown_label
        self._total_count = sum(counts.values())

        if self._total_count == 0:
            raise ValueError(
                'PSI baseline for column "{}" contains no usable observations. '
                'A baseline must contain data before batches can be compared.'.format(column_name))

        if smoothing == 0 and any(count == 0 for count in self._counts.values()):
            raise ValueError(
                'PSI baseline for column "{}" has a zero-count bin and smoothing is 0, so PSI would be undefined. '
                'Set smoothing to a positive value.'.format(column_name))

    @classmethod
    def from_categories(cls, column_name, values, categories, *, smoothing=DEFAULT_SMOOTHING,
                        missing_policy=MISSING_SEPARATE, unknown_label=DEFAULT_UNKNOWN_LABEL):
        """
        Declare a categorical baseline.

        :param column_name:
            The name of the column this baseline describes.
        :param values:
            The baseline values, typically ``table.columns[column_name].values()``.
        :param categories:
            The complete, ordered sequence of accepted category values.
            Categories absent from the baseline still get a zero-count bin so
            later batches can report movement into them.
        :param smoothing:
            Non-negative count added to every bin before proportions are
            calculated. Defaults to :data:`DEFAULT_SMOOTHING` (0.5).
        :param missing_policy:
            One of :data:`MISSING_SEPARATE` (default), :data:`MISSING_EXCLUDE`
            or :data:`MISSING_UNKNOWN`.
        :param unknown_label:
            Label of the reserved bin for values outside the declared
            categories. Must not itself be a declared category.
        """
        categories = tuple(categories)

        if not categories:
            raise ValueError('At least one category must be declared in a PSIBaseline.')

        if any(category is None for category in categories):
            raise ValueError('None is reserved for the missing bin and can not be a declared category.')

        if unknown_label is None:
            raise ValueError('unknown_label can not be None; None is reserved for the missing bin.')

        if len(set(categories)) != len(categories):
            raise ValueError('PSI baseline categories must be unique.')

        if unknown_label in categories:
            raise ValueError('unknown_label "{}" collides with a declared category.'.format(unknown_label))

        smoothing = _normalize_smoothing(smoothing)
        _validate_missing_policy(missing_policy)

        bin_keys = list(categories)
        bin_labels = [str(category) for category in categories]
        bin_keys.append(unknown_label)
        bin_labels.append(unknown_label)

        if missing_policy == MISSING_SEPARATE:
            bin_keys.append(None)
            bin_labels.append(None)

        counts = OrderedDict((key, 0) for key in bin_keys)

        for value in values:
            key = _assign_categorical(value, categories, unknown_label, missing_policy)

            if key is not _EXCLUDE:
                counts[key] += 1

        return cls(column_name, 'categorical', bin_keys, bin_labels, counts, None,
                   categories, smoothing, missing_policy, unknown_label)

    @classmethod
    def from_bins(cls, column_name, values, boundaries, *, smoothing=DEFAULT_SMOOTHING,
                  missing_policy=MISSING_SEPARATE, unknown_label=DEFAULT_UNKNOWN_LABEL):
        """
        Declare a numeric baseline from explicit bin boundaries.

        :param boundaries:
            A strictly increasing sequence of at least two numbers. With
            boundaries ``(b0, b1, ..., bn)`` the bins are ``[b0, b1)``,
            ``[b1, b2)``, ..., ``[b(n-1), bn]`` (the final bin is closed on
            both sides). Values outside ``[b0, bn]`` go to the unknown bin.
        """
        decimal_boundaries = tuple(_as_decimal(value, 'boundary') for value in boundaries)

        if len(decimal_boundaries) < 2:
            raise ValueError('At least two boundaries are required to define PSI bins.')

        if unknown_label is None:
            raise ValueError('unknown_label can not be None; None is reserved for the missing bin.')

        if any(decimal_boundaries[i] >= decimal_boundaries[i + 1]
               for i in range(len(decimal_boundaries) - 1)):
            raise ValueError('PSI bin boundaries must be strictly increasing.')

        smoothing = _normalize_smoothing(smoothing)
        _validate_missing_policy(missing_policy)

        bin_keys = []
        bin_labels = []

        for i in range(1, len(decimal_boundaries)):
            low = decimal_boundaries[i - 1]
            high = decimal_boundaries[i]
            bin_keys.append((low, high))
            right_bracket = ']' if i == len(decimal_boundaries) - 1 else ')'
            bin_labels.append('[{}, {}{}'.format(low, high, right_bracket))

        bin_keys.append(unknown_label)
        bin_labels.append(unknown_label)

        if missing_policy == MISSING_SEPARATE:
            bin_keys.append(None)
            bin_labels.append(None)

        counts = OrderedDict((key, 0) for key in bin_keys)

        for value in values:
            key = _assign_numeric(value, decimal_boundaries, unknown_label, missing_policy)

            if key is not _EXCLUDE:
                counts[key] += 1

        return cls(column_name, 'numeric', bin_keys, bin_labels, counts,
                   decimal_boundaries, None, smoothing, missing_policy, unknown_label)

    def assign(self, value):
        """
        Return the fixed bin key for a value under this baseline, or
        :data:`_EXCLUDE` if the missing policy excludes it.
        """
        if self._kind == 'categorical':
            return _assign_categorical(value, self._categories, self._unknown_label, self._missing_policy)

        return _assign_numeric(value, self._boundaries, self._unknown_label, self._missing_policy)

    @property
    def column_name(self):
        return self._column_name

    @property
    def kind(self):
        return self._kind

    @property
    def bin_keys(self):
        return self._bin_keys

    @property
    def bin_labels(self):
        return self._bin_labels

    @property
    def counts(self):
        return OrderedDict(self._counts)

    @property
    def smoothing(self):
        return self._smoothing

    @property
    def missing_policy(self):
        return self._missing_policy

    @property
    def unknown_label(self):
        return self._unknown_label

    @property
    def total_count(self):
        return self._total_count

    @property
    def config(self):
        """
        Audit record of everything that was declared when building the
        baseline: bins, smoothing and missing-value policy.
        """
        return {
            'column_name': self._column_name,
            'kind': self._kind,
            'categories': self._categories,
            'boundaries': self._boundaries,
            'bin_labels': self._bin_labels,
            'smoothing': self._smoothing,
            'missing_policy': self._missing_policy,
            'unknown_label': self._unknown_label,
            'baseline_total': self._total_count,
        }


def _normalize_smoothing(smoothing):
    smoothing = _as_decimal(smoothing, 'smoothing')

    if smoothing < 0:
        raise ValueError('PSI smoothing must be non-negative.')

    return smoothing


def _validate_missing_policy(missing_policy):
    if missing_policy not in MISSING_POLICIES:
        raise ValueError('missing_policy must be one of {}.'.format(', '.join(MISSING_POLICIES)))


def _assign_categorical(value, categories, unknown_label, missing_policy):
    if value is None:
        if missing_policy == MISSING_EXCLUDE:
            return _EXCLUDE
        if missing_policy == MISSING_UNKNOWN:
            return unknown_label

        return None

    if value in categories:
        return value

    return unknown_label


def _assign_numeric(value, boundaries, unknown_label, missing_policy):
    if value is None:
        if missing_policy == MISSING_EXCLUDE:
            return _EXCLUDE
        if missing_policy == MISSING_UNKNOWN:
            return unknown_label

        return None

    if value < boundaries[0] or value > boundaries[-1]:
        return unknown_label

    last_index = len(boundaries) - 1

    for i in range(1, len(boundaries)):
        if value < boundaries[i] or i == last_index:
            return boundaries[i - 1], boundaries[i]

    return unknown_label


class PSIResult:
    """
    The result of a :class:`PSI` comparison.

    Contains the per-bin contribution table, the total PSI value and the
    policy metadata needed to interpret the result. When
    :attr:`status` is :data:`PSI_STATUS_EMPTY_BATCH`, :attr:`psi` is
    :code:`None`: an empty batch is inconclusive and must never be reported
    as "no drift".
    """
    def __init__(self, baseline, table, contributions, psi, status, batch_total,
                 has_unknown_values, zero_frequency_bins):
        self._baseline = baseline
        self._table = table
        self._contributions = contributions
        self._psi = psi
        self._status = status
        self._batch_total = batch_total
        self._has_unknown_values = has_unknown_values
        self._zero_frequency_bins = tuple(zero_frequency_bins)

    @property
    def baseline(self):
        return self._baseline

    @property
    def table(self):
        """
        A :class:`.Table` with one row per fixed bin and columns for both
        counts, both smoothed proportions and the per-bin PSI contribution.
        """
        return self._table

    @property
    def contributions(self):
        """
        An :class:`collections.OrderedDict` of bin label to contribution.
        Contribution values are :code:`None` for an empty batch.
        """
        return OrderedDict(self._contributions)

    @property
    def psi(self):
        """
        The total Population Stability Index, or :code:`None` for an empty
        batch.
        """
        return self._psi

    @property
    def status(self):
        return self._status

    @property
    def batch_total(self):
        return self._batch_total

    @property
    def has_unknown_values(self):
        """
        Whether the batch contained non-null values outside the declared
        categories or boundaries (new categories or out-of-range numbers).
        """
        return self._has_unknown_values

    @property
    def zero_frequency_bins(self):
        """
        Labels of bins with a zero raw batch count. Their proportions come
        entirely from the smoothing parameter.
        """
        return self._zero_frequency_bins

    @property
    def smoothing(self):
        return self._baseline.smoothing

    @property
    def missing_policy(self):
        return self._baseline.missing_policy

    def __str__(self):
        if self._status == PSI_STATUS_EMPTY_BATCH:
            return ('PSI for "{}": not calculated (empty batch; no usable observations). '
                    'This is inconclusive, not evidence of no drift.').format(self._baseline.column_name)

        return 'PSI for "{}": {} ({} bins, smoothing={})'.format(
            self._baseline.column_name, self._psi, len(self._baseline.bin_keys), self._baseline.smoothing)


class PSI(Aggregation):
    """
    Calculate the Population Stability Index of a column against a fixed
    :class:`PSIBaseline`.

    PSI compares the distribution of a batch with the baseline distribution
    over the baseline's fixed bins::

        PSI = sum((p_batch - p_baseline) * ln(p_batch / p_baseline))

    The baseline declares the categories or numeric boundaries once; every
    later batch reuses the same bins. New categories and out-of-range numeric
    values are counted in a reserved unknown bin (reported via
    :attr:`.PSIResult.has_unknown_values`), nulls follow the baseline's
    missing-value policy, and zero-frequency bins are kept by adding the
    recorded smoothing count to every bin before proportions are calculated.

    This aggregation only reports the metric and its decomposition. It does
    not define pass/fail thresholds, which are a business decision.

    This aggregation can not be applied to a :class:`.TableSet`.

    :param baseline:
        A :class:`PSIBaseline` created from a reference table.
    """
    def __init__(self, baseline):
        if not isinstance(baseline, PSIBaseline):
            raise ValueError('PSI requires a PSIBaseline instance.')

        self._baseline = baseline

    def validate(self, table):
        column = table.columns[self._baseline.column_name]

        if self._baseline.kind == 'numeric' and not isinstance(column.data_type, Number):
            raise DataTypeError('PSI with numeric boundaries can only be applied to columns containing Number data.')

    def run(self, table):
        from agate import Table

        column = table.columns[self._baseline.column_name]
        baseline = self._baseline

        batch_counts = OrderedDict((key, 0) for key in baseline.bin_keys)
        has_unknown_values = False

        for value in column.values():
            key = baseline.assign(value)

            if key is _EXCLUDE:
                continue

            if value is not None and key == baseline.unknown_label:
                has_unknown_values = True

            batch_counts[key] += 1

        batch_total = sum(batch_counts.values())

        if has_unknown_values:
            warnings.warn(
                'Column "{}" contains values outside the declared PSI bins. They are counted in the "{}" bin.'.format(
                    column.name, baseline.unknown_label),
                PSINewCategoryWarning, stacklevel=2)

        if batch_total == 0:
            warnings.warn(
                'Table contains no usable observations for PSI on column "{}". '
                'PSI can not be calculated; an empty batch is inconclusive, not evidence of no drift.'.format(
                    column.name),
                PSIEmptyBatchWarning, stacklevel=2)

            return self._build_result(Table, batch_counts, batch_total, has_unknown_values, empty=True)

        return self._build_result(Table, batch_counts, batch_total, has_unknown_values, empty=False)

    def _build_result(self, table_class, batch_counts, batch_total, has_unknown_values, empty):
        baseline = self._baseline
        smoothing = baseline.smoothing
        bin_count = len(baseline.bin_keys)

        baseline_adjusted_total = baseline.total_count + smoothing * bin_count
        batch_adjusted_total = batch_total + smoothing * bin_count

        rows = []
        contributions = OrderedDict()
        psi = Decimal('0')
        zero_frequency_bins = []

        for key, label in zip(baseline.bin_keys, baseline.bin_labels):
            baseline_count = baseline.counts[key]
            batch_count = batch_counts[key]

            if batch_count == 0:
                zero_frequency_bins.append(label)

            baseline_proportion = (Decimal(baseline_count) + smoothing) / baseline_adjusted_total

            if empty:
                batch_proportion = None
                contribution = None
            else:
                batch_proportion = (Decimal(batch_count) + smoothing) / batch_adjusted_total

                if smoothing == 0 and batch_proportion == 0:
                    raise ValueError(
                        'A batch bin has zero proportion and smoothing is 0, so PSI is undefined for column "{}". '
                        'Rebuild the PSIBaseline with a positive smoothing value.'.format(baseline.column_name))

                contribution = (batch_proportion - baseline_proportion) * _log(
                    batch_proportion / baseline_proportion)
                psi += contribution

            contributions[label] = contribution
            rows.append([
                label,
                baseline_count,
                batch_count,
                baseline_proportion,
                batch_proportion,
                contribution,
            ])

        result_table = table_class(
            rows,
            ('bin', 'baseline_count', 'batch_count', 'baseline_proportion', 'batch_proportion', 'psi_contribution'),
            (Text(), Number(), Number(), Number(), Number(), Number()))

        status = PSI_STATUS_EMPTY_BATCH if empty else PSI_STATUS_OK

        return PSIResult(baseline, result_table, contributions, None if empty else psi,
                         status, batch_total, has_unknown_values, zero_frequency_bins)
