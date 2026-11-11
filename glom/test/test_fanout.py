import pytest

from itertools import count

from glom import (glom, T, Check, SKIP, Fanout, FanoutRun, Outlet,
                  BLOCK, DROP_NEWEST, Route,
                  ROUTED, BACKPRESSURE, EXHAUSTED, ALL_CLOSED)
from glom.streaming import BackpressureFull
from glom import Iter


RECORDS = [
    {'id': 1, 'score': 90, 'ok': True},
    {'id': 2, 'score': 5, 'ok': False},
    {'id': 3, 'score': 70, 'ok': True},
]


def _qualification():
    return Check(T['score'], validate=lambda score: score >= 60)


def test_fanout_basic_routing_and_multi_hit():
    fanout = Fanout([
        Outlet('accepted', Check(T['ok'], equal_to=True)),
        Outlet('high_scores', Check(T['score'], validate=lambda s: s > 50)),
    ])
    run = glom(RECORDS, fanout)

    routed = list(run)
    assert [r.record['id'] for r in routed] == [1, 2, 3]
    # records 1 and 3 are qualified AND high-scoring, so both outlets get them
    assert run.drain('accepted') == [RECORDS[0], RECORDS[2]]
    assert run.drain('high_scores') == [RECORDS[0], RECORDS[2]]


def test_fanout_subspec_runs_once_per_record():
    seen = []

    def enrich(record):
        seen.append(record['id'])
        return dict(record, enriched=True)

    fanout = Fanout([
        Outlet('a', lambda r: True),
        Outlet('b', lambda r: True),
    ], subspec=enrich)
    run = glom(RECORDS, fanout)
    run.run()

    assert seen == [1, 2, 3]  # one enrichment per record, not per outlet
    assert all(msg['enriched'] for msg in run.drain('a'))
    assert all(msg['enriched'] for msg in run.drain('b'))


def test_fanout_shared_spec_evaluated_once_per_record():
    calls = []

    def rule(record):
        calls.append(record['id'])
        return record['score'] >= 60

    fanout = Fanout([
        Outlet('accepted', rule),
        Outlet('rejected', rule, invert=True, include_reason=True),
    ])
    run = glom(RECORDS, fanout)
    run.run()

    # the shared rule object ran exactly once for each record despite two outlets
    assert calls == [1, 2, 3]
    assert [msg['id'] for msg in run.drain('accepted')] == [1, 3]
    rejected = run.drain('rejected')
    assert [msg['id'] for msg, _reason in rejected] == [2]


def test_fanout_rejection_reasons_from_check():
    fanout = Fanout([
        Outlet('accepted', _qualification()),
        Outlet('rejected', _qualification(), invert=True, include_reason=True),
    ])
    run = glom(RECORDS, fanout)
    run.run()

    rejected = run.drain('rejected')
    assert len(rejected) == 1
    result, reason = rejected[0]
    assert result['id'] == 2
    assert isinstance(reason, str) and reason  # a concrete, non-empty reason

    # the qualified outlet carries bare results, no reason tuples
    assert run.drain('accepted') == [RECORDS[0], RECORDS[2]]


def test_fanout_routes_describe_each_outlet():
    fanout = Fanout([
        Outlet('accepted', _qualification()),
        Outlet('rejected', _qualification(), invert=True),
    ])
    run = glom(RECORDS, fanout)
    routed = list(run)

    first = routed[0]
    assert first.record == RECORDS[0]
    assert first.result == RECORDS[0]
    assert {route.name: route.matched for route in first.routes} == \
        {'accepted': True, 'rejected': False}
    assert all(isinstance(route, Route) for route in first.routes)

    # the failing record is routed to the rejection outlet with a reason
    failing = next(r for r in routed if r.record['id'] == 2)
    rejected_route = next(r for r in failing.routes if r.name == 'rejected')
    assert rejected_route.matched is True
    assert rejected_route.reason


def test_fanout_skip_rule_is_non_match():
    # Check(default=SKIP) is glom's idiomatic filter; SKIP must mean "no route"
    rule = Check(T['ok'], equal_to=True, default=SKIP)
    run = glom(RECORDS, Fanout([Outlet('only_ok', rule)]))
    run.run()
    assert [msg['id'] for msg in run.drain('only_ok')] == [1, 3]


def test_fanout_callable_exception_becomes_reason():
    def boom(record):
        raise ValueError('kaboom')

    run = glom(RECORDS, Fanout([
        Outlet('ok', lambda r: True),
        Outlet('errors', boom, invert=True, include_reason=True),
    ]))
    run.run()
    messages = run.drain('errors')
    assert all(reason == 'kaboom' for _result, reason in messages)
    assert len(messages) == 3


def test_fanout_block_backpressure_pauses_source():
    source = iter(range(100))
    run = glom(source, Fanout([Outlet('o', lambda x: True, max_size=3)]))

    statuses = [run.run_once() for _ in range(4)]
    assert statuses == [ROUTED, ROUTED, ROUTED, BACKPRESSURE]
    assert run.blocked_outlet.name == 'o'
    # the source was not pulled again while the outlet was backed up
    assert next(source) == 3

    run.drain('o')
    assert run.run_once() == ROUTED  # resumes after room is made


def test_fanout_run_raises_instead_of_reading_whole_input():
    source = count()  # infinite; reading it all would hang forever
    run = glom(source, Fanout([Outlet('o', lambda x: True, max_size=5)]))

    with pytest.raises(BackpressureFull) as excinfo:
        run.run()
    assert excinfo.value.outlet.name == 'o'
    assert run.records_processed == 5
    assert next(source) == 5  # only the buffered records were consumed


def test_fanout_drop_newest_policy():
    run = glom(range(20), Fanout([
        Outlet('o', lambda x: True, max_size=3, on_full=DROP_NEWEST)]))
    run.run()

    outlet = run.outlets[0]
    assert run.drain('o') == [0, 1, 2]
    assert outlet.delivered == 3
    assert outlet.dropped == 17


def test_fanout_callable_on_full_policy():
    overflow = []

    def on_full(outlet, message):
        overflow.append(message)

    run = glom(range(6), Fanout([
        Outlet('o', lambda x: True, max_size=2, on_full=on_full)]))
    run.run()

    assert run.drain('o') == [0, 1]
    assert overflow == [2, 3, 4, 5]


def test_fanout_all_closed_stops_consuming_source():
    consumed = []

    def source():
        for value in range(5):
            consumed.append(value)
            yield value

    run = glom(source(), Fanout([
        Outlet('a', lambda x: True), Outlet('b', lambda x: True)]))
    for outlet in run.outlets:
        outlet.close()

    assert run.run_once() == ALL_CLOSED
    assert consumed == []  # nothing pulled while every outlet is closed

    run.outlets[0].open()
    assert run.run_once() == ROUTED
    assert consumed == [0]

    run.run()
    assert consumed == [0, 1, 2, 3, 4]
    assert run.drain('a') == [0, 1, 2, 3, 4]
    assert run.drain('b') == []  # never reopened, so never received anything


def test_fanout_closing_one_outlet_keeps_others_flowing():
    run = glom(RECORDS, Fanout([
        Outlet('a', lambda r: True), Outlet('b', lambda r: True)]))
    run.outlets[1].close()
    run.run()

    assert [msg['id'] for msg in run.drain('a')] == [1, 2, 3]
    assert run.drain('b') == []


def test_fanout_exhaustion_is_terminal_and_resumable():
    run = glom([1, 2], Fanout([Outlet('o', lambda x: True)]))
    assert run.run_once() == ROUTED
    assert run.run_once() == ROUTED
    assert run.run_once() == EXHAUSTED
    assert run.run_once() == EXHAUSTED  # stays exhausted, no error
    assert run.drain('o') == [1, 2]


def test_fanout_drain_unknown_outlet():
    run = glom([1], Fanout([Outlet('o', lambda x: True)]))
    with pytest.raises(KeyError):
        run.drain('nope')


def test_fanout_streams_without_eager_read():
    # an unbounded source must not be eagerly consumed just by applying glom
    run = glom(count(), Fanout([Outlet('o', lambda x: True, max_size=10)]))
    assert isinstance(run, FanoutRun)
    assert run.records_processed == 0
    run.run_once()
    assert run.records_processed == 1


def test_fanout_validation():
    with pytest.raises(ValueError):
        Fanout([])
    with pytest.raises(ValueError):
        Fanout([Outlet('same', T), Outlet('same', T)])
    with pytest.raises(TypeError):
        Outlet('o', T, max_size=0)
    with pytest.raises(TypeError):
        Outlet('o', T, on_full='not-a-policy')


def test_fanout_repr():
    outlet = Outlet('rejected', _qualification(), invert=True, include_reason=True)
    fanout = Fanout([Outlet('accepted', _qualification()), outlet])
    assert repr(fanout).startswith('Fanout([')
    assert 'invert=True' in repr(outlet)
    run = glom(RECORDS, fanout)
    assert 'FanoutRun(' in repr(run)


def test_iter_pipeline_still_works():
    # regression guard: the pre-existing Iter pipeline is untouched
    assert list(glom(range(5), Iter(lambda x: x * 2).filter(lambda x: x > 4))) == [6, 8]
    assert glom(range(5), Iter().map(lambda x: x + 1).all()) == [1, 2, 3, 4, 5]
