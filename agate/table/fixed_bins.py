from collections import OrderedDict

from agate.data_types import Number, Text
from agate.tableset import TableSet


class BinningResult:
    """
    The result of applying a :class:`.BinSpec` to a table with
    :meth:`.Table.fixed_bins`.

    Exposes both the records that fell into each bucket and the per-bucket
    counts. Buckets with no records are preserved: they appear as empty
    tables in :attr:`BinningResult.tables` and as rows with a count of zero
    in :attr:`BinningResult.counts`.
    """

    def __init__(self, spec, column_name, tables):
        self._spec = spec
        self._column_name = column_name
        self._tables = tables

    @property
    def spec(self):
        """
        The :class:`.BinSpec` that produced this result.
        """
        return self._spec

    @property
    def tables(self):
        """
        A :class:`.TableSet` mapping each bucket label (the bin labels, plus
        the underflow, overflow and null labels) to a :class:`.Table`
        containing the records in that bucket. Empty buckets are included as
        empty tables.
        """
        return self._tables

    @property
    def counts(self):
        """
        A :class:`.Table` with one row per bucket, in distribution order
        (underflow, bins from lowest to highest, overflow, null). The first
        column is named after the binned column and holds the bucket label;
        the second is named :code:`Count` and holds the number of records.
        Empty buckets are included with a count of zero.
        """
        from agate.table import Table

        rows = []
        row_names = []

        for label, table in self._tables.items():
            rows.append([label, len(table.rows)])
            row_names.append(label)

        return Table(
            rows,
            [self._column_name, 'Count'],
            [Text(), Number()],
            row_names=row_names
        )

    def __repr__(self):
        return f'<BinningResult column={self._column_name!r} buckets={len(self._tables)}>'


def fixed_bins(self, column_name, spec):
    """
    Bin the values in a column using a fixed, reusable :class:`.BinSpec`
    instead of edges derived from the data.

    Unlike :meth:`.Table.bins`, the boundaries come entirely from the spec,
    so distributions produced on different days or from different tables are
    directly comparable. Values below the smallest edge, above the largest
    edge, or missing are counted in their own buckets rather than being
    forced into the first or last bin. Buckets with no matching rows are
    preserved.

    The resulting :class:`.BinningResult` exposes both the records in each
    bucket (:attr:`.BinningResult.tables`) and the per-bucket counts
    (:attr:`.BinningResult.counts`).

    :param column_name:
        The name of the column to bin. Should be of type :class:`.Number`.
    :param spec:
        A :class:`.BinSpec` describing the fixed edges, the edge inclusivity
        rule and the bucket labels.
    :returns:
        A :class:`.BinningResult`.
    """
    buckets = OrderedDict((label, []) for label in spec.bucket_labels())

    for row in self.rows:
        buckets[spec.assign(row[column_name])].append(row)

    tables = []
    keys = []

    for label, rows in buckets.items():
        tables.append(self._fork(rows))
        keys.append(label)

    tableset = TableSet(tables, keys, key_name=column_name, key_type=Text())

    return BinningResult(spec, column_name, tableset)
