from datetime import date, timedelta

import pytest

from boltons.interutils import Interval, IntervalMap

DAY = timedelta(days=1)
D = date


def test_construction_validation():
    with pytest.raises(ValueError):
        Interval(10, 1)
    with pytest.raises(TypeError):
        Interval(1, 2, lower_inclusive='yes')
    with pytest.raises(ValueError):
        Interval(1, 2, step=1, domain='continuous')
    with pytest.raises(ValueError):
        Interval(1, 2, domain='occasional')
    with pytest.raises(TypeError):
        Interval(1, 2, domain=object())
    with pytest.raises(ValueError):
        Interval(1, 2, step=2, domain=type('Grid', (), {'step': 3})())


def test_from_bounded_rejects_unknown_bound():
    Interval.from_bounded(D(2024, 1, 1), D(2024, 1, 31), step=DAY)
    with pytest.raises(ValueError):
        Interval.from_bounded(D(2024, 1, 1), None, step=DAY)
    with pytest.raises(ValueError):
        Interval.from_bounded(None, D(2024, 1, 31), step=DAY)
    # The bare constructor still allows a deliberately unbounded range.
    assert not Interval(D(2024, 1, 1), None, step=DAY).is_bounded


def test_membership_endpoint_inclusivity():
    closed = Interval(1, 3)
    assert 1 in closed and 2 in closed and 3 in closed
    assert 0 not in closed and 4 not in closed

    opened = Interval(1, 3, lower_inclusive=False, upper_inclusive=False)
    assert 1 not in opened and 3 not in opened and 2 in opened

    half = Interval(D(2024, 1, 1), D(2024, 1, 10),
                    upper_inclusive=False, step=DAY)
    assert D(2024, 1, 1) in half
    assert D(2024, 1, 9) in half
    assert D(2024, 1, 10) not in half


def test_membership_respects_discrete_grid():
    evens = Interval(0, 10, step=2)
    assert 0 in evens and 4 in evens and 10 in evens
    assert 1 not in evens and 3 not in evens and 9 not in evens
    assert 11 not in evens

    daily = Interval(D(2024, 1, 1), D(2024, 1, 7), step=DAY)
    noon_membership_days = [D(2024, 1, d) for d in range(1, 8)]
    assert all(day in daily for day in noon_membership_days)
    assert D(2023, 12, 31) not in daily

    unbounded_lower = Interval(None, D(2024, 1, 10), step=DAY)
    assert D(2000, 1, 1) in unbounded_lower
    assert D(2024, 1, 10) in unbounded_lower
    assert D(2024, 1, 11) not in unbounded_lower


def test_unbounded_intervals():
    forever = Interval(D(2024, 1, 1), None, step=DAY)
    assert D(2030, 1, 1) in forever
    assert D(2023, 12, 31) not in forever
    assert forever.closed_lower == D(2024, 1, 1)
    assert forever.closed_upper is None

    whole_line = Interval(None, None)
    assert -10**9 in whole_line and 10**9 in whole_line
    assert None not in whole_line


def test_empty_intervals_explain_themselves():
    disjoint_intersection = Interval(1, 5).intersect(Interval(10, 20))
    assert disjoint_intersection.is_empty
    assert 'no members' in disjoint_intersection.empty_reason

    open_singleton = Interval(1, 1, lower_inclusive=False)
    assert open_singleton.is_empty
    assert open_singleton.empty_reason

    discrete_open_singleton = Interval(
        D(2024, 5, 1), D(2024, 5, 1), lower_inclusive=False, step=DAY)
    assert discrete_open_singleton.is_empty
    assert discrete_open_singleton.empty_reason

    closed_singleton = Interval(D(2024, 5, 1), D(2024, 5, 1), step=DAY)
    assert not closed_singleton.is_empty
    assert D(2024, 5, 1) in closed_singleton


def test_closed_bounds_on_discrete_domain():
    opened = Interval(D(2024, 1, 1), D(2024, 1, 10),
                      lower_inclusive=False, upper_inclusive=False, step=DAY)
    assert opened.closed_lower == D(2024, 1, 2)
    assert opened.closed_upper == D(2024, 1, 9)

    with pytest.raises(TypeError):
        Interval(0.0, 1.0).closed_lower


def test_discrete_adjacency_merges_but_gaps_do_not():
    adjacent_dates = Interval(D(2024, 1, 1), D(2024, 3, 31), step=DAY) \
        .union(Interval(D(2024, 4, 1), D(2024, 6, 30),
                        upper_inclusive=False, step=DAY))
    assert isinstance(adjacent_dates, Interval)
    assert D(2024, 3, 31) in adjacent_dates
    assert D(2024, 4, 1) in adjacent_dates
    assert D(2024, 6, 29) in adjacent_dates
    assert D(2024, 6, 30) not in adjacent_dates

    adjacent_ints = Interval(1, 3, step=1).union(Interval(4, 6, step=1))
    assert isinstance(adjacent_ints, Interval)
    assert [value for value in range(0, 8) if value in adjacent_ints] == \
        [1, 2, 3, 4, 5, 6]

    gap = Interval(1, 3, step=1).union(Interval(5, 6, step=1))
    assert isinstance(gap, IntervalMap)
    assert 4 not in gap and len(gap) == 2


def test_continuous_adjacency_rules():
    touching_closed = IntervalMap([Interval(0, 1), Interval(1, 2)])
    assert len(touching_closed) == 1
    assert 1.0 in touching_closed

    touching_open = IntervalMap([
        Interval(0, 1, upper_inclusive=False),
        Interval(1, 2, lower_inclusive=False)])
    assert len(touching_open) == 2
    assert 1.0 not in touching_open
    assert 0.999 in touching_open and 1.001 in touching_open


def test_interval_intersection_endpoints():
    result = Interval(0.0, 2.0).intersect(
        Interval(1.0, 3.0, lower_inclusive=False))
    assert 1.0 not in result
    assert 1.5 in result and 2.0 in result and 3.0 not in result

    dates = Interval(D(2024, 1, 1), D(2024, 6, 30), step=DAY).intersect(
        Interval(D(2024, 3, 15), D(2024, 12, 31),
                 lower_inclusive=False, step=DAY))
    assert D(2024, 3, 15) not in dates
    assert D(2024, 3, 16) in dates and D(2024, 6, 30) in dates


def test_interval_difference():
    pieces = Interval(1, 10, step=1).difference(Interval(4, 6, step=1))
    assert [value for value in range(1, 11) if value in pieces] == \
        [1, 2, 3, 7, 8, 9, 10]
    assert len(pieces) == 2

    open_removed = Interval(0, 10).difference(
        Interval(3, 7, lower_inclusive=False, upper_inclusive=False))
    assert 3.0 in open_removed and 7.0 in open_removed
    assert 5.0 not in open_removed

    full_removal = Interval(1, 5, step=1).difference(Interval(0, 10, step=1))
    assert not full_removal
    assert full_removal.empty_intervals


def test_interval_map_normalizes_and_indexes():
    mapping = IntervalMap([
        Interval(D(2025, 1, 5), D(2025, 1, 10), step=DAY),
        Interval(D(2025, 1, 1), D(2025, 1, 4), step=DAY),
        Interval(D(2025, 1, 11), D(2025, 1, 12), step=DAY)])
    assert len(mapping) == 1
    assert mapping.bounds == (D(2025, 1, 1), D(2025, 1, 12))
    assert mapping.get_interval(D(2025, 1, 7)).lower == D(2025, 1, 1)
    assert mapping.get_interval(D(2024, 12, 31)) is None

    with_unbounded_head = IntervalMap([
        Interval(None, D(2024, 1, 1), step=DAY),
        Interval(D(2025, 1, 1), D(2025, 1, 2), step=DAY)])
    assert D(2000, 1, 1) in with_unbounded_head
    assert D(2024, 6, 1) not in with_unbounded_head
    assert D(2025, 1, 1) in with_unbounded_head


def test_interval_map_set_operations():
    first = IntervalMap([Interval(1, 10, step=1),
                         Interval(20, 30, step=1)])
    second = IntervalMap([Interval(5, 22, step=1)])

    union = first.union(second)
    assert [v for v in range(0, 32) if v in union] == list(range(1, 31))

    intersection = first.intersection(second)
    assert [v for v in range(0, 32) if v in intersection] == \
        list(range(5, 11)) + list(range(20, 23))

    difference = first.difference(second)
    assert [v for v in range(0, 32) if v in difference] == \
        [1, 2, 3, 4, 23, 24, 25, 26, 27, 28, 29, 30]

    disjoint = IntervalMap([Interval(100, 110, step=1)]) \
        .intersection(IntervalMap([Interval(0, 5, step=1)]))
    assert not disjoint
    assert disjoint.empty_intervals
    assert all(iv.is_empty for iv in disjoint.empty_intervals)


def test_retention_policy_boundary_days_survive_chained_ops():
    # Two retention windows written with different endpoint conventions.
    policy = IntervalMap([
        Interval.from_bounded(D(2023, 1, 1), D(2023, 12, 31), step=DAY),
        Interval.from_bounded(D(2024, 6, 1), D(2024, 12, 31),
                              upper_inclusive=False, step=DAY)])

    legal_hold = Interval.from_bounded(
        D(2023, 6, 1), D(2024, 6, 30), lower_inclusive=False, step=DAY)

    retained = policy.union(legal_hold)
    deletable = policy.difference(legal_hold)

    boundary_days = [
        D(2023, 1, 1), D(2023, 6, 1), D(2023, 6, 2),
        D(2023, 12, 31), D(2024, 1, 1), D(2024, 5, 31),
        D(2024, 6, 1), D(2024, 6, 30), D(2024, 7, 1),
        D(2024, 12, 30), D(2024, 12, 31), D(2025, 1, 1)]
    expected_retained = [
        True, True, True, True, True, True,
        True, True, True, True, False, False]
    for day, expected in zip(boundary_days, expected_retained):
        assert (day in retained) is expected
        assert (day in retained) == (day in policy or day in legal_hold)

    assert D(2023, 1, 1) in deletable
    assert D(2023, 6, 2) not in deletable
    assert D(2024, 7, 1) in deletable
    assert D(2024, 12, 31) not in deletable


def test_domain_compatibility_errors():
    with pytest.raises(TypeError):
        Interval(1, 2, step=1).intersect(Interval(1.0, 2.0))
    with pytest.raises(TypeError):
        Interval(1, 2, step=1).intersect(Interval(1, 2, step=2))
    with pytest.raises(TypeError):
        Interval(1, 2).intersect(3)
    with pytest.raises(TypeError):
        IntervalMap([(1, 2)])


def test_invalid_step_rejected():
    with pytest.raises(ValueError):
        Interval(1, 5, step=0)
    with pytest.raises(ValueError):
        Interval(1, 5, step=-1)
    with pytest.raises(ValueError):
        Interval(D(2024, 1, 1), D(2024, 1, 5), step=timedelta(days=0))


def test_interval_and_interval_map_interoperate():
    single = Interval(1, 10, step=1)
    mapping = IntervalMap([Interval(5, 15, step=1),
                           Interval(100, 110, step=1)])

    assert [v for v in range(0, 20) if v in single.intersect(mapping)] == \
        list(range(5, 11))
    assert [v for v in range(0, 20) if v in (single & mapping)] == \
        list(range(5, 11))
    assert [v for v in range(0, 20) if v in (mapping & single)] == \
        list(range(5, 11))

    union = single | mapping
    assert 1 in union and 15 in union and 50 not in union and 105 in union

    difference = single.difference(mapping)
    assert [v for v in range(0, 12) if v in difference] == [1, 2, 3, 4]
    reflected = mapping - single
    assert [v for v in range(0, 16) if v in reflected] == \
        list(range(11, 16))
    assert 105 in reflected


def test_equality_hash_and_repr():
    first = Interval(1, 3, step=1)
    second = Interval(1, 3, step=1)
    third = Interval(1, 3, lower_inclusive=False, step=1)
    assert first == second
    assert first != third
    assert hash(first) == hash(second)
    assert repr(first).startswith('Interval([1, 3]')

    left_map = IntervalMap([Interval(1, 3, step=1)])
    right_map = IntervalMap([Interval(1, 3, step=1)])
    assert left_map == right_map
    assert IntervalMap([]) == IntervalMap([])
    assert 'IntervalMap' in repr(left_map)
