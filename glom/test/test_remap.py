from collections import OrderedDict

import pytest

from glom import glom, remap, Remap, REJECT, COLLECT, MERGE, T, SKIP, Check, Coalesce
from glom import (RemapError, KeyTransformError, ValueTransformError,
                  KeyCollisionError, RemapEntry)


def _norm_product(key):
    # partner feeds spell the same product different ways:
    # 'A-01', 'A_01', and 'a01' all normalize to 'a01'
    return key.replace('-', '').replace('_', '').lower()


def test_remap_basic_key_and_value_transforms():
    target = {'A-01': '10', 'B-02': '20'}
    spec = Remap(key=str.lower, value=int)
    assert glom(target, spec) == {'a-01': 10, 'b-02': 20}
    # input is not mutated
    assert target == {'A-01': '10', 'B-02': '20'}


def test_remap_preserves_mapping_type_and_order():
    target = OrderedDict([('B', 1), ('a', 2), ('C', 3)])
    result = glom(target, Remap(key=str.lower))
    assert type(result) is OrderedDict
    assert list(result) == ['b', 'a', 'c']


def test_remap_empty_mapping():
    assert glom({}, Remap(key=str.lower)) == {}


def test_remap_default_spec_is_passthrough():
    target = {'a': 1, 'b': 2}
    assert glom(target, Remap()) == target


def test_remap_rejects_collision_by_default():
    target = {'A-01': {'qty': 1}, 'a01': {'qty': 2}}
    with pytest.raises(KeyCollisionError) as exc_info:
        glom(target, Remap(key=_norm_product))
    err = exc_info.value
    assert err.key == 'a01'
    assert err.orig_keys == ('A-01', 'a01')
    assert [entry.orig_key for entry in err.entries] == ['A-01', 'a01']
    assert [entry.value for entry in err.entries] == [{'qty': 1}, {'qty': 2}]
    assert all(entry.error is None for entry in err.entries)
    assert 'A-01' in err.get_message() and 'a01' in err.get_message()


def test_remap_explicit_reject_matches_default():
    target = {'A': 1, 'a': 2}
    with pytest.raises(KeyCollisionError):
        glom(target, Remap(key=str.lower, on_collision=REJECT))


def test_remap_collect_groups_colliding_values_in_order():
    target = {'A-01': 1, 'a01': 2, 'B-02': 3}
    result = glom(target, Remap(key=_norm_product, on_collision=COLLECT))
    assert result == {'a01': [1, 2], 'b02': 3}


def test_remap_collect_keeps_scalar_for_non_colliding_keys():
    target = {'A-01': 1, 'B-02': 2}
    result = glom(target, Remap(key=str.lower, on_collision=COLLECT))
    assert result == {'a-01': 1, 'b-02': 2}


def test_remap_merge_with_custom_merger():
    target = {'A-01': {'qty': 1}, 'a01': {'qty': 2}, 'B-02': {'qty': 5}}
    spec = Remap(key=_norm_product, on_collision=MERGE,
                 merge=lambda values: {'qty': sum(v['qty'] for v in values)})
    assert glom(target, spec) == {'a01': {'qty': 3}, 'b02': {'qty': 5}}


def test_remap_merge_defaults_to_mapping_update():
    target = {'A': {'x': 1}, 'a': {'y': 2}}
    result = glom(target, Remap(key=str.lower, on_collision=MERGE))
    assert result == {'a': {'x': 1, 'y': 2}}


def test_remap_merge_passes_values_in_input_order():
    seen = []

    def merger(values):
        seen.extend(values)
        return 'merged'

    target = {'A-01': 'first', 'a01': 'second'}
    glom(target, Remap(key=_norm_product, on_collision=MERGE, merge=merger))
    assert seen == ['first', 'second']


def test_remap_key_transform_error_keeps_orig_key():
    def explode(key):
        raise RuntimeError('key exploded')

    with pytest.raises(KeyTransformError) as exc_info:
        glom({'A-01': 1}, Remap(key=explode))
    err = exc_info.value
    assert err.orig_key == 'A-01'
    assert isinstance(err.exc, RuntimeError)
    assert 'A-01' in err.get_message()


def test_remap_value_transform_error_keeps_both_keys():
    with pytest.raises(ValueTransformError) as exc_info:
        glom({'A-01': 'nope'}, Remap(key=str.lower, value=int))
    err = exc_info.value
    assert err.orig_key == 'A-01'
    assert err.norm_key == 'a-01'
    assert isinstance(err.exc, ValueError)
    message = err.get_message()
    assert 'A-01' in message and 'a-01' in message


def test_remap_key_and_value_errors_are_distinct_types():
    def bad_key(key):
        raise RuntimeError('key exploded')

    with pytest.raises(KeyTransformError):
        glom({'A-01': 1}, Remap(key=bad_key, value=int))
    with pytest.raises(ValueTransformError):
        glom({'A-01': 'nope'}, Remap(value=int))


def test_remap_aggregates_all_errors_in_one_pass():
    def keyfn(key):
        if key == 'badkey':
            raise RuntimeError('key exploded')
        return key.lower()

    target = {'A-01': 'nope', 'a01': 2, 'badkey': 1, 'B-02': 3}
    with pytest.raises(RemapError) as exc_info:
        glom(target, Remap(key=keyfn, value=int))
    err = exc_info.value
    assert len(err.errors) == 2
    assert [type(e) for e in err.errors] == [KeyTransformError, ValueTransformError]
    assert [e.orig_key for e in err.key_errors] == ['badkey']
    assert [e.orig_key for e in err.value_errors] == ['A-01']
    assert err.collision_errors == []
    assert '2 error(s)' in err.get_message()


def test_remap_reports_collision_alongside_transform_errors():
    def keyfn(key):
        if key == 'badkey':
            raise RuntimeError('key exploded')
        return _norm_product(key)

    target = {'A-01': 1, 'a01': 2, 'badkey': 3}
    with pytest.raises(RemapError) as exc_info:
        glom(target, Remap(key=keyfn))
    err = exc_info.value
    assert len(err.key_errors) == 1
    assert len(err.collision_errors) == 1
    assert err.collision_errors[0].orig_keys == ('A-01', 'a01')


def test_remap_evaluates_every_value_transform_even_on_collision():
    evaluated = []

    def record(value):
        evaluated.append(value)
        return int(value)

    target = {'A-01': '1', 'a01': '2'}
    glom(target, Remap(key=_norm_product, value=record, on_collision=COLLECT))
    assert evaluated == ['1', '2']


def test_remap_does_not_silently_drop_failed_value_on_collision():
    target = {'A-01': 'bad', 'a01': '2'}
    with pytest.raises(ValueTransformError) as exc_info:
        glom(target, Remap(key=_norm_product, value=int, on_collision=COLLECT))
    assert exc_info.value.orig_key == 'A-01'
    assert exc_info.value.norm_key == 'a01'


def test_remap_unhashable_normalized_key_is_key_error():
    with pytest.raises(KeyTransformError) as exc_info:
        glom({'A-01': 1}, Remap(key=lambda key: ['unhashable']))
    err = exc_info.value
    assert err.orig_key == 'A-01'
    assert err.norm_key == ['unhashable']
    assert isinstance(err.exc, TypeError)


def test_remap_non_mapping_target_raises_type_error():
    with pytest.raises(TypeError, match='expected Remap target to be a mapping'):
        glom([('a', 1)], Remap())


def test_remap_with_subspec():
    target = {'payload': {'A-01': 1, 'b-02': 2}}
    result = glom(target, Remap(key=str.lower, spec='payload'))
    assert result == {'a-01': 1, 'b-02': 2}


def test_remap_composes_with_other_glom_specs():
    target = {'rows': [{'A': 1}, {'B': 2}]}
    result = glom(target, ('rows', [Remap(key=str.lower)]))
    assert result == [{'a': 1}, {'b': 2}]


def test_remap_convenience_function():
    target = {'A-01': 1, 'B-02': 2}
    assert remap(target, key=str.lower, value=lambda v: v * 10) == {'a-01': 10, 'b-02': 20}


def test_remap_repr():
    assert repr(Remap()) == 'Remap()'
    assert repr(Remap(key=str.lower, on_collision=COLLECT)) == (
        "Remap(<method 'lower' of 'str' objects>, on_collision=COLLECT)")
    merger = lambda values: values
    rendered = repr(Remap(key=str.lower, on_collision=MERGE, merge=merger))
    assert rendered.startswith("Remap(<method 'lower' of 'str' objects>, on_collision=MERGE, merge=")
    # default merger is not rendered
    assert 'merge=' not in repr(Remap(key=str.lower, on_collision=MERGE))


def test_remap_constructor_validation():
    with pytest.raises(ValueError, match='on_collision'):
        Remap(on_collision='nope')
    with pytest.raises(TypeError, match='merge to be callable'):
        Remap(on_collision=MERGE, merge='not-callable')
    with pytest.raises(ValueError, match='only supported with on_collision=MERGE'):
        Remap(on_collision=COLLECT, merge=sum)
    with pytest.raises(ValueError, match='only supported with on_collision=MERGE'):
        Remap(merge=sum)


def test_remap_entry_namedtuple():
    entry = RemapEntry('A-01', 'a-01', None, ValueError('x'))
    assert entry.orig_key == 'A-01'
    assert entry.norm_key == 'a-01'
    assert entry.value is None
    assert isinstance(entry.error, ValueError)


def test_remap_error_categories_on_single_failures():
    key_err = KeyTransformError('k', RuntimeError('x'))
    assert key_err.key_errors and not key_err.value_errors and not key_err.collision_errors
    val_err = ValueTransformError('k', 'kk', ValueError('x'))
    assert val_err.value_errors and not val_err.key_errors


def test_remap_accepts_full_glomspecs_not_just_callables():
    target = {'A-01': '10', 'B-02': '20'}
    # tuple pipeline as the value spec, T expression as the key spec
    spec = Remap(key=T.lower().replace('-', ''), value=(int, lambda v: v + 1))
    assert glom(target, spec) == {'a01': 11, 'b02': 21}


def test_remap_works_with_top_level_default():
    target = {'A-01': 1, 'a01': 2}
    result = glom(target, Remap(key=_norm_product), default={'failed': True})
    assert result == {'failed': True}


def test_remap_inside_dict_spec():
    target = {'rows': {'A-01': 1, 'B-02': 2}}
    result = glom(target, {'normalized': ('rows', Remap(key=_norm_product))})
    assert result == {'normalized': {'a01': 1, 'b02': 2}}


def test_remap_skips_entries_whose_value_spec_returns_SKIP():
    target = {'A-01': 1, 'B-02': None, 'C-03': 3}
    spec = Remap(key=_norm_product,
                 value=Coalesce(Check(), default=SKIP))
    assert glom(target, spec) == {'a01': 1, 'c03': 3}


def test_remap_skip_does_not_count_toward_collision():
    target = {'A-01': 1, 'a01': None}
    spec = Remap(key=_norm_product,
                 value=Coalesce(Check(), default=SKIP))
    assert glom(target, spec) == {'a01': 1}


def test_remap_skip_with_collect_leaves_scalar():
    target = {'A-01': 1, 'a01': None, 'B-02': 2}
    spec = Remap(key=_norm_product, on_collision=COLLECT,
                 value=Coalesce(Check(), default=SKIP))
    assert glom(target, spec) == {'a01': 1, 'b02': 2}


def test_remap_three_way_collision_records_all_origins():
    target = {'A-01': 1, 'a01': 2, 'A_01': 3}
    with pytest.raises(KeyCollisionError) as exc_info:
        glom(target, Remap(key=_norm_product))
    assert exc_info.value.orig_keys == ('A-01', 'a01', 'A_01')
    assert [e.value for e in exc_info.value.entries] == [1, 2, 3]


def test_remap_value_error_entry_survives_into_collision_record():
    target = {'A-01': 'bad', 'a01': 2}
    with pytest.raises(RemapError) as exc_info:
        glom(target, Remap(key=_norm_product, value=int))
    err = exc_info.value
    assert len(err.value_errors) == 1
    collision = err.collision_errors[0]
    assert collision.orig_keys == ('A-01', 'a01')
    failed_entry = next(e for e in collision.entries if e.error is not None)
    assert failed_entry.orig_key == 'A-01'
    assert isinstance(failed_entry.error, ValueError)
    good_entry = next(e for e in collision.entries if e.error is None)
    assert good_entry.value == 2
