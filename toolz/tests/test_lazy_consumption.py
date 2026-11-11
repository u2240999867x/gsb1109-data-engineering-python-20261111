"""Consumption-count tests for lazy pipelines.

When handling sensitive input, downstream stages must stop reading from the
source as soon as they have what they need.  These tests combine
``map``/``filter``/``take`` with ``pipe`` and assert the *exact* number of
``__next__`` calls made against the source, so a pipeline that produces the
correct final list but reads too much data is still caught.
"""
import pytest
from functools import partial

from toolz import pipe, take


class SourceError(Exception):
    """Raised by CountingSource when it reaches its failure position."""


class CountingSource:
    """Iterator that records every ``__next__`` call.

    ``calls`` counts all ``__next__`` invocations (including the one that
    raises ``StopIteration`` or ``SourceError``); ``pulled`` holds the values
    actually handed out.  If ``fail_at`` is given, the ``fail_at``-th call
    (0-based) raises ``SourceError`` instead of returning a value.
    """

    def __init__(self, data, fail_at=None):
        self.data = list(data)
        self.fail_at = fail_at
        self.calls = 0
        self.pulled = []

    def __iter__(self):
        return self

    def __next__(self):
        index = self.calls
        self.calls += 1
        if self.fail_at is not None and index == self.fail_at:
            raise SourceError(index)
        if index >= len(self.data):
            raise StopIteration
        value = self.data[index]
        self.pulled.append(value)
        return value


def inc(x):
    return x + 1


def iseven(x):
    return x % 2 == 0


def test_take_zero_reads_nothing():
    src = CountingSource(range(10))
    result = pipe(src, partial(map, inc), partial(take, 0), list)
    assert result == []
    assert src.calls == 0
    assert src.pulled == []


def test_take_reads_exactly_n():
    src = CountingSource(range(10))
    result = pipe(src, partial(map, inc), partial(take, 3), list)
    assert result == [1, 2, 3]
    # Same list must not come at the cost of a 4th read.
    assert src.calls == 3
    assert src.pulled == [0, 1, 2]


def test_map_after_take_reads_exactly_n():
    src = CountingSource(range(10))
    result = pipe(src, partial(take, 3), partial(map, inc), list)
    assert result == [1, 2, 3]
    assert src.calls == 3


def test_first_item_filtered_reads_one_extra_only():
    src = CountingSource([1, 2, 4, 6, 8])
    # Only the head is filtered out, so two results need exactly three reads.
    result = pipe(src, partial(filter, iseven), partial(take, 2), list)
    assert result == [2, 4]
    assert src.calls == 3
    assert src.pulled == [1, 2, 4]


def test_filter_take_stops_before_draining_source():
    src = CountingSource(range(100))
    result = pipe(src, partial(filter, iseven), partial(take, 2), list)
    assert result == [0, 2]
    assert src.calls == 3
    assert src.pulled == [0, 1, 2]


def test_exhaustive_filter_reads_to_stop_iteration():
    src = CountingSource([1, 3, 5])
    result = pipe(src, partial(filter, iseven), list)
    assert result == []
    # Three values plus the final StopIteration.
    assert src.calls == 4
    assert src.pulled == [1, 3, 5]


def test_downstream_failure_stops_consumption():
    src = CountingSource(range(10), fail_at=2)
    with pytest.raises(SourceError):
        pipe(src, partial(map, inc), partial(take, 5), list)
    # Two successful reads plus the failing call, and nothing after.
    assert src.calls == 3
    assert src.pulled == [0, 1]


def test_failure_while_filtering_head():
    src = CountingSource([1, 2, 3], fail_at=0)
    with pytest.raises(SourceError):
        pipe(src, partial(filter, iseven), partial(take, 1), list)
    assert src.calls == 1
    assert src.pulled == []


def test_failure_not_retried_and_no_further_reads():
    src = CountingSource(range(10), fail_at=1)
    it = pipe(src, partial(map, inc), partial(take, 4))
    assert next(it) == 1
    with pytest.raises(SourceError):
        next(it)
    calls_after_failure = src.calls
    assert calls_after_failure == 2
    # The failure must not trigger extra reads on the source.
    assert src.calls == calls_after_failure
    assert src.pulled == [0]


def test_early_close_abandons_source_without_more_reads():
    src = CountingSource(range(10))
    it = pipe(src, partial(map, inc), partial(take, 4))
    assert next(it) == 1
    assert src.calls == 1
    # Downstream walks away after one item; the source must stay unread.
    del it
    assert src.calls == 1
    assert src.pulled == [0]


def test_same_result_does_not_hide_overreading():
    src = CountingSource(range(10))
    result = pipe(src, partial(take, 3), list)
    assert result == [0, 1, 2] == list(range(3))
    # The result matches the first three items, but that is only correct
    # if exactly three reads happened -- a 4th read would be an over-read.
    assert src.calls == 3
    assert src.pulled == result
