import io
import json

import pytest

from glom import glom, Spec, BadSpec
from glom.spec_data import (DataSpecError, DataSpecRegistry, load_data_spec,
                            loads_data_spec, load_data_spec_file,
                            register_data_op)


@pytest.fixture
def registry():
    reg = DataSpecRegistry()
    reg.register_op('strip', str.strip)
    reg.register_op('upper', str.upper)
    reg.register_op('join', lambda sep, parts: sep.join(parts))
    reg.register_op('answer', lambda: 42)
    return reg


TARGET = {'name': '  acme ', 'tags': ['a', 'b', 'c'], 'vip': True,
          'items': [{'k': 'v1'}, {'k': 'v2'}], 'nested': {'count': 3}}


def test_mapping_and_paths(registry):
    spec = load_data_spec({
        'name': {'path': ['name']},
        'first_tag': {'path': ['tags', 0]},
        'count': {'path': ['nested', 'count']},
    }, registry=registry)
    assert isinstance(spec, Spec)
    assert spec.glom(TARGET) == {'name': '  acme ', 'first_tag': 'a',
                                 'count': 3}


def test_empty_path_is_current_target(registry):
    spec = load_data_spec(
        {'name': {'op': 'strip', 'args': [{'path': []}]}}, registry=registry)
    assert spec.glom('  hi  ') == {'name': 'hi'}


def test_list_spec(registry):
    spec = load_data_spec(
        {'ks': {'pipe': [{'path': ['items']}, [{'path': ['k']}]]}},
        registry=registry)
    assert spec.glom(TARGET) == {'ks': ['v1', 'v2']}


def test_literal_values(registry):
    spec = load_data_spec({
        's': {'value': 'literal'},
        'n': {'value': 7},
        'flag': {'value': False},
        'nothing': {'value': None},
        'container': {'value': [1, {'x': None}, True]},
    }, registry=registry)
    assert spec.glom(TARGET) == {
        's': 'literal', 'n': 7, 'flag': False, 'nothing': None,
        'container': [1, {'x': None}, True]}


def test_registered_op_args_and_kwargs(registry):
    by_args = load_data_spec(
        {'tags': {'op': 'join',
                  'args': [{'value': '-'}, {'path': ['tags']}]}},
        registry=registry)
    assert by_args.glom(TARGET) == {'tags': 'a-b-c'}

    by_kwargs = load_data_spec(
        {'tags': {'op': 'join',
                  'kwargs': {'sep': {'value': '.'},
                             'parts': {'path': ['tags']}}}},
        registry=registry)
    assert by_kwargs.glom(TARGET) == {'tags': 'a.b.c'}


def test_zero_arg_op(registry):
    spec = load_data_spec({'v': {'op': 'answer'}}, registry=registry)
    assert spec.glom(TARGET) == {'v': 42}


def test_coalesce(registry):
    spec = load_data_spec(
        {'v': {'coalesce': [{'path': ['missing']},
                            {'path': ['nested', 'count']}]}},
        registry=registry)
    assert spec.glom(TARGET) == {'v': 3}

    spec_default = load_data_spec(
        {'v': {'coalesce': [{'path': ['missing']}, {'value': 'd'}]}},
        registry=registry)
    assert spec_default.glom(TARGET) == {'v': 'd'}


def test_conditional(registry):
    config = {'kind': {'if': {'path': ['vip']},
                       'then': {'value': 'vip'},
                       'else': {'value': 'standard'}}}
    spec = load_data_spec(config, registry=registry)
    assert spec.glom(TARGET) == {'kind': 'vip'}
    assert spec.glom({'vip': False}) == {'kind': 'standard'}


def test_conditional_without_else_skips(registry):
    spec = load_data_spec(
        {'a': {'if': {'value': False}, 'then': {'value': 1}},
         'b': {'value': 2}}, registry=registry)
    assert spec.glom(TARGET) == {'b': 2}

    list_spec = load_data_spec(
        [{'if': {'path': ['keep']}, 'then': {'path': ['v']}}],
        registry=registry)
    assert list_spec.glom([{'keep': True, 'v': 1},
                           {'keep': False, 'v': 2}]) == [1]


def test_pipe(registry):
    spec = load_data_spec(
        {'name': {'pipe': [{'path': ['name']},
                           {'op': 'strip', 'args': [{'path': []}]},
                           {'op': 'upper', 'args': [{'path': []}]}]}},
        registry=registry)
    assert spec.glom(TARGET) == {'name': 'ACME'}


def test_map_escape_hatch_for_reserved_field_names(registry):
    spec = load_data_spec(
        {'map': {'if': {'value': 1}, 'then': {'value': 2}}},
        registry=registry)
    assert spec.glom(TARGET) == {'if': 1, 'then': 2}


def test_empty_mapping(registry):
    spec = load_data_spec({'a': {}}, registry=registry)
    assert spec.glom(TARGET) == {'a': {}}
    assert load_data_spec({}, registry=registry).glom(TARGET) == {}


def test_unregistered_op_reports_full_location(registry):
    with pytest.raises(DataSpecError, match=r"\$\.outer\[0\].*'rm_rf'"):
        load_data_spec({'outer': [{'op': 'rm_rf'}]}, registry=registry)

    with pytest.raises(DataSpecError, match=r"\$\.a\.b.*'nope'"):
        load_data_spec({'a': {'b': {'op': 'nope'}}}, registry=registry)

    with pytest.raises(DataSpecError, match="Registered operations:"):
        load_data_spec({'op': 'ghost'}, registry=registry)


def test_bare_string_rejected(registry):
    with pytest.raises(DataSpecError, match=r"\$\.a.*bare string"):
        load_data_spec({'a': 'name.upper'}, registry=registry)


def test_callable_and_non_data_values_rejected(registry):
    with pytest.raises(DataSpecError, match=r"\$\.a\.value"):
        load_data_spec({'a': {'value': str.upper}}, registry=registry)

    with pytest.raises(DataSpecError, match=r"\$\.a\.value"):
        load_data_spec({'a': {'value': {1, 2}}}, registry=registry)


def test_path_validation(registry):
    with pytest.raises(DataSpecError, match=r"\$\.a\.path\[1\]"):
        load_data_spec({'a': {'path': ['x', 1.5]}}, registry=registry)
    with pytest.raises(DataSpecError, match=r"\$\.a\.path"):
        load_data_spec({'a': {'path': 'x'}}, registry=registry)
    with pytest.raises(DataSpecError, match=r"\$\.a\.path\[0\]"):
        load_data_spec({'a': {'path': [True]}}, registry=registry)


def test_op_node_validation(registry):
    with pytest.raises(DataSpecError, match=r"\$\.a\.args"):
        load_data_spec({'a': {'op': 'strip', 'args': {}}}, registry=registry)
    with pytest.raises(DataSpecError, match=r"\$\.a\.kwargs"):
        load_data_spec({'a': {'op': 'strip', 'kwargs': []}}, registry=registry)
    with pytest.raises(DataSpecError, match=r"unknown key"):
        load_data_spec({'a': {'op': 'strip', 'bogus': 1}}, registry=registry)
    with pytest.raises(DataSpecError, match=r'"op" must be a string'):
        load_data_spec({'a': {'op': 5}}, registry=registry)


def test_conditional_validation(registry):
    with pytest.raises(DataSpecError, match=r"\$\.a\.if"):
        load_data_spec({'a': {'if': {'value': 1}}}, registry=registry)
    with pytest.raises(DataSpecError, match=r"\$\.a.*only keys"):
        load_data_spec({'a': {'if': {'value': 1},
                              'then': {'value': 2}, 'x': 1}},
                       registry=registry)


def test_empty_list_and_bad_mapping_keys(registry):
    with pytest.raises(DataSpecError, match=r"\$\.a.*empty list"):
        load_data_spec({'a': []}, registry=registry)
    with pytest.raises(DataSpecError, match=r"mapping keys must be strings"):
        load_data_spec({1: {'value': 2}}, registry=registry)


def test_registry_isolation():
    reg_a = DataSpecRegistry()
    reg_a.register_op('only_in_a', lambda: 'a')
    reg_b = DataSpecRegistry()
    assert 'only_in_a' not in reg_b.registered_ops
    with pytest.raises(DataSpecError, match="unregistered operation"):
        load_data_spec({'v': {'op': 'only_in_a'}}, registry=reg_b)
    assert load_data_spec({'v': {'op': 'only_in_a'}},
                          registry=reg_a).glom({}) == {'v': 'a'}


def test_register_op_validation(registry):
    with pytest.raises(TypeError):
        registry.register_op(5, lambda: 0)
    with pytest.raises(TypeError):
        registry.register_op('', lambda: 0)
    with pytest.raises(TypeError):
        registry.register_op('ok', 7)


def test_default_registry_helper():
    register_data_op('fixture_constant_op', lambda: 'ok')
    assert load_data_spec({'v': {'op': 'fixture_constant_op'}}).glom({}) == \
        {'v': 'ok'}


def test_loads_json(registry):
    text = json.dumps({'name': {'op': 'strip',
                                'args': [{'path': ['name']}]}})
    spec = loads_data_spec(text, registry=registry)
    assert spec.glom(TARGET) == {'name': 'acme'}


def test_loads_yaml(registry):
    text = """
    name:
      op: strip
      args:
        - path: [name]
    """
    spec = loads_data_spec(text, format='yaml', registry=registry)
    assert spec.glom(TARGET) == {'name': 'acme'}


def test_yaml_unsafe_tags_blocked(registry):
    with pytest.raises(DataSpecError, match="failed to parse YAML"):
        loads_data_spec(
            "a: !!python/object/apply:os.system ['echo blocked']",
            format='yaml', registry=registry)


def test_bad_json_and_unknown_format(registry):
    with pytest.raises(DataSpecError, match="failed to parse JSON"):
        loads_data_spec('{not valid', registry=registry)
    with pytest.raises(DataSpecError, match='unsupported data spec format'):
        loads_data_spec('x', format='toml', registry=registry)


def test_load_file_infers_format(tmp_path, registry):
    path = tmp_path / 'mapping.json'
    path.write_text(json.dumps({'v': {'path': ['x']}}))
    with open(str(path)) as fh:
        spec = load_data_spec_file(fh, registry=registry)
    assert spec.glom({'x': 9}) == {'v': 9}

    yaml_file = io.StringIO("v:\n  path: [x]\n")
    yaml_file.name = 'mapping.yaml'
    assert load_data_spec_file(yaml_file, registry=registry).glom(
        {'x': 1}) == {'v': 1}

    unknown = io.StringIO('x')
    unknown.name = 'mapping.txt'
    with pytest.raises(DataSpecError, match="could not infer"):
        load_data_spec_file(unknown, registry=registry)


def test_data_spec_error_is_bad_spec():
    assert issubclass(DataSpecError, BadSpec)


def test_python_spec_api_unchanged():
    # callables and lambdas remain fully supported through the regular API
    assert glom({'n': 2}, {'n': ('n', lambda value: value * 3)}) == {'n': 6}
    with pytest.raises(DataSpecError):
        load_data_spec(object(), registry=DataSpecRegistry())
    with pytest.raises(TypeError):
        load_data_spec({}, registry=object())
