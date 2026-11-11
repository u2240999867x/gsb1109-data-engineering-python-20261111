import copy
import pickle

import pytest

from boltons.typeutils import make_sentinel, get_all_subclasses, _sentinel_registries

NOT_SET = make_sentinel('not_set', var_name='NOT_SET')

def test_sentinel_falsiness():
    assert not NOT_SET


def test_sentinel_pickle():
    assert pickle.dumps(NOT_SET)

def test_sentinel_copy():
    test = make_sentinel('test')
    assert test is copy.copy(test)
    assert test is copy.deepcopy(test)


def test_sentinel_unregistered_ephemeral():
    first = make_sentinel('ephemeral')
    second = make_sentinel('ephemeral')
    assert first is not second
    assert first != second
    assert not first
    assert repr(first) == "Sentinel('ephemeral')"


def test_sentinel_registry_identity():
    first = make_sentinel('missing', registry='test_reg_identity')
    second = make_sentinel('missing', registry='test_reg_identity')
    assert first is second
    assert not first
    assert repr(first) == "Sentinel('missing', registry='test_reg_identity')"


def test_sentinel_registry_namespacing():
    first = make_sentinel('missing', registry='test_reg_a')
    second = make_sentinel('missing', registry='test_reg_b')
    assert first is not second
    assert first != second


def test_sentinel_registry_copy():
    sentinel = make_sentinel('missing', registry='test_reg_copy')
    assert copy.copy(sentinel) is sentinel
    assert copy.deepcopy(sentinel) is sentinel


def test_sentinel_registry_pickle():
    sentinel = make_sentinel('missing', registry='test_reg_pickle')
    assert pickle.loads(pickle.dumps(sentinel)) is sentinel


def test_sentinel_registry_unregistered_unpickle():
    sentinel = make_sentinel('missing', registry='test_reg_unpickle')
    data = pickle.dumps(sentinel)
    # simulate a process where the sentinel was never registered
    saved = _sentinel_registries.pop('test_reg_unpickle')
    try:
        with pytest.raises(ValueError, match='test_reg_unpickle'):
            pickle.loads(data)
    finally:
        _sentinel_registries['test_reg_unpickle'] = saved


def test_sentinel_var_name_and_registry_conflict():
    with pytest.raises(ValueError, match='var_name'):
        make_sentinel('conflict', var_name='CONFLICT', registry='test_reg_conflict')


def test_get_all_subclasses_metaclass():
    class Meta(type):
        pass

    class SubMeta(Meta):
        pass

    assert get_all_subclasses(Meta) == [SubMeta]
    assert get_all_subclasses(SubMeta) == []
