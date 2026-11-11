import json
from bisect import bisect_left, bisect_right
from decimal import Decimal

from babel.numbers import format_decimal

from agate import utils


class BinSpec:
    """
    A reusable, fixed set of bin boundaries ("edges") that can be saved and
    applied to any number of :class:`.Table` instances via
    :meth:`.Table.fixed_bins`.

    Because the edges are stored explicitly, distributions generated on
    different days or from different tables remain comparable: values are
    never re-binned against the extremes of a new dataset.

    Values below the smallest edge, above the largest edge, or missing
    (:code:`None`) are not forced into a bin. They are assigned to the
    dedicated :attr:`BinSpec.UNDERFLOW`, :attr:`BinSpec.OVERFLOW` and
    :attr:`BinSpec.NULL` buckets instead, so they can be counted separately.

    Which bin a value exactly equal to an edge falls into is determined by
    the :code:`closed` argument:

    * :code:`'left'` (the default): each bin contains its lower edge, so a
      value equal to an interior edge belongs to the bin *above* it.
    * :code:`'right'`: each bin contains its upper edge, so a value equal to
      an interior edge belongs to the bin *below* it.

    In both cases the outermost edges are always included in the first/last
    bin, so a value exactly equal to the minimum or maximum edge is never
    counted as underflow or overflow.

    Labels are purely cosmetic. Assignment is defined only by the edges and
    the :code:`closed` rule, so renaming labels (see :meth:`BinSpec.with_labels`)
    never changes which bin a value belongs to.

    :param edges:
        A sequence of at least two numbers defining the bin boundaries. Must
        be strictly increasing. Values are stored as :class:`Decimal`.
    :param labels:
        An optional sequence of :code:`len(edges) - 1` display names for the
        bins. If not specified, labels are generated from the edges, e.g.
        :code:`[0 - 100)`. Labels must be unique.
    :param closed:
        Either :code:`'left'` (bins include their lower edge) or
        :code:`'right'` (bins include their upper edge).
    :param underflow_label:
        The label used for values below the smallest edge.
    :param overflow_label:
        The label used for values above the largest edge.
    :param null_label:
        The label used for missing (:code:`None`) values.
    """

    #: Logical bucket key returned by :meth:`BinSpec.locate` for values below
    #: the smallest edge.
    UNDERFLOW = 'underflow'

    #: Logical bucket key returned by :meth:`BinSpec.locate` for values above
    #: the largest edge.
    OVERFLOW = 'overflow'

    #: Logical bucket key returned by :meth:`BinSpec.locate` for missing
    #: (:code:`None`) values.
    NULL = 'null'

    def __init__(self, edges, labels=None, closed='left', underflow_label='underflow',
                 overflow_label='overflow', null_label=None):
        if closed not in ('left', 'right'):
            raise ValueError('closed must be either "left" or "right".')

        try:
            edges = tuple(e if isinstance(e, Decimal) else Decimal(str(e)) for e in edges)
        except ArithmeticError:
            raise ValueError('edges must be numbers.') from None

        if len(edges) < 2:
            raise ValueError('At least two edges are required to define a bin.')

        if any(edges[i] >= edges[i + 1] for i in range(len(edges) - 1)):
            raise ValueError('edges must be strictly increasing.')

        if labels is None:
            labels = self._default_labels(edges, closed)
        else:
            labels = tuple(labels)

            if len(labels) != len(edges) - 1:
                raise ValueError('labels must contain exactly one name per bin (%i).' % (len(edges) - 1))

        bucket_labels = (underflow_label,) + tuple(labels) + (overflow_label, null_label)

        if len(set(bucket_labels)) != len(bucket_labels):
            raise ValueError('Bin, underflow, overflow and null labels must all be unique.')

        self._edges = edges
        self._labels = tuple(labels)
        self._closed = closed
        self._underflow_label = underflow_label
        self._overflow_label = overflow_label
        self._null_label = null_label

    @staticmethod
    def _default_labels(edges, closed):
        decimal_places = utils.max_precision(edges)
        formatter = utils.make_number_formatter(decimal_places)

        def format_edge(edge):
            return format_decimal(edge, format=formatter)

        labels = []
        last = len(edges) - 2

        for i in range(last + 1):
            low = format_edge(edges[i])
            high = format_edge(edges[i + 1])

            if closed == 'left':
                label = f'[{low} - {high}]' if i == last else f'[{low} - {high})'
            else:
                label = f'[{low} - {high}]' if i == 0 else f'({low} - {high}]'

            labels.append(label)

        return labels

    @property
    def edges(self):
        """
        The bin boundaries as a tuple of :class:`Decimal` values.
        """
        return self._edges

    @property
    def labels(self):
        """
        The display labels of the bins, in edge order.
        """
        return self._labels

    @property
    def closed(self):
        """
        Which side of each bin is closed: :code:`'left'` or :code:`'right'`.
        """
        return self._closed

    @property
    def underflow_label(self):
        """
        The label used for values below the smallest edge.
        """
        return self._underflow_label

    @property
    def overflow_label(self):
        """
        The label used for values above the largest edge.
        """
        return self._overflow_label

    @property
    def null_label(self):
        """
        The label used for missing (:code:`None`) values.
        """
        return self._null_label

    def bucket_labels(self):
        """
        All bucket labels in distribution order: underflow, then each bin from
        lowest to highest, then overflow, then null. Suitable to be used as keys
        when binning a table.
        """
        return (self._underflow_label,) + self._labels + (self._overflow_label, self._null_label)

    def locate(self, value):
        """
        Locate the bucket a value belongs to, by logic rather than by label.

        :param value:
            A number or :code:`None`.
        :returns:
            The integer index of the bin (counting from zero) for values
            within the edges, or one of :attr:`BinSpec.UNDERFLOW`,
            :attr:`BinSpec.OVERFLOW` and :attr:`BinSpec.NULL`.
        """
        if value is None:
            return BinSpec.NULL

        if not isinstance(value, Decimal):
            value = Decimal(str(value))

        if value < self._edges[0]:
            return BinSpec.UNDERFLOW

        if value > self._edges[-1]:
            return BinSpec.OVERFLOW

        if self._closed == 'left':
            return min(bisect_right(self._edges, value) - 1, len(self._edges) - 2)

        return max(bisect_left(self._edges, value) - 1, 0)

    def assign(self, value):
        """
        Resolve a value to the label of its bucket.

        :param value:
            A number or :code:`None`.
        :returns:
            The bin label, or the underflow, overflow or null label.
        """
        key = self.locate(value)

        if key == BinSpec.UNDERFLOW:
            return self._underflow_label

        if key == BinSpec.OVERFLOW:
            return self._overflow_label

        if key == BinSpec.NULL:
            return self._null_label

        return self._labels[key]

    _UNSET = object()

    def with_labels(self, labels, underflow_label=_UNSET, overflow_label=_UNSET, null_label=_UNSET):
        """
        Create a copy of this spec with different display labels.

        Assignment depends only on the edges and the :code:`closed` rule, so
        renaming labels never changes which bucket a value belongs to.

        Special bucket labels that are not specified are copied from this
        spec.

        :param labels:
            The new bin labels. Must contain exactly one name per bin.
        :param underflow_label:
            Optionally, a new label for the underflow bucket.
        :param overflow_label:
            Optionally, a new label for the overflow bucket.
        :param null_label:
            Optionally, a new label for the null bucket.
        :returns:
            A new :class:`BinSpec`.
        """
        return BinSpec(
            self._edges,
            labels,
            closed=self._closed,
            underflow_label=self._underflow_label if underflow_label is self._UNSET else underflow_label,
            overflow_label=self._overflow_label if overflow_label is self._UNSET else overflow_label,
            null_label=self._null_label if null_label is self._UNSET else null_label
        )

    def to_dict(self):
        """
        Serialize this spec to a plain dictionary, suitable for JSON. Edges
        are stored as strings so no decimal precision is lost.
        """
        return {
            'edges': [str(edge) for edge in self._edges],
            'labels': list(self._labels),
            'closed': self._closed,
            'underflow_label': self._underflow_label,
            'overflow_label': self._overflow_label,
            'null_label': self._null_label
        }

    @classmethod
    def from_dict(cls, data):
        """
        Recreate a spec from a dictionary produced by :meth:`BinSpec.to_dict`.
        """
        return cls(
            data['edges'],
            labels=data.get('labels'),
            closed=data.get('closed', 'left'),
            underflow_label=data.get('underflow_label', 'underflow'),
            overflow_label=data.get('overflow_label', 'overflow'),
            null_label=data.get('null_label')
        )

    def to_json(self, **kwargs):
        """
        Serialize this spec to a JSON string. :code:`kwargs` are passed on to
        :func:`json.dumps`.
        """
        return json.dumps(self.to_dict(), **kwargs)

    @classmethod
    def from_json(cls, data):
        """
        Recreate a spec from a JSON string produced by :meth:`BinSpec.to_json`.
        """
        return cls.from_dict(json.loads(data))

    def save(self, path):
        """
        Save this spec to a JSON file so the same boundaries can be reused
        across runs and applied to different tables.

        :param path:
            The filesystem path to write to.
        """
        with open(path, 'w') as f:
            f.write(self.to_json(indent=2))

    @classmethod
    def load(cls, path):
        """
        Load a spec previously written with :meth:`BinSpec.save`.

        :param path:
            The filesystem path to read from.
        :returns:
            A new :class:`BinSpec`.
        """
        with open(path) as f:
            return cls.from_json(f.read())

    def __eq__(self, other):
        return isinstance(other, BinSpec) and self.to_dict() == other.to_dict()

    def __repr__(self):
        return f'<BinSpec edges={list(self._edges)} closed={self._closed!r}>'
