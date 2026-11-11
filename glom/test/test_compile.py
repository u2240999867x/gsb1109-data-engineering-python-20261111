import pytest

from glom import (glom, compile, Glommer, CompiledSpec, T, S, Path,
                  Coalesce, Fill, Val, BadSpec, GlomError, PathAccessError,
                  register)
from glom.core import Let


class Box:
    def __init__(self, items):
        self.items = items


class ModuleBox:
    def __init__(self, items):
        self.items = items


def _equiv_cases():
    return [
        ({'a': {'b': 'c'}}, 'a.b'),
        ({'a': {'b': 'c'}}, Path('a', 'b')),
        ({'a': 1}, T['a'] + 1),
        ({'a': {'b': 'c', 'd': 'e'}, 'h': [0, 1, 2]},
         {'a': 'a.b', 'd': 'a.d', 'h': ('h', [lambda x: x * 2])}),
        ({'c': 'd'}, Coalesce('a', 'b', 'c')),
        ({'data': [0, 2, 4]}, Fill((T['data'][2], Val('x')))),
        ({'a': 1}, (S(x=T['a']), S['x'])),
    ]


def test_compile_matches_glom_entrypoint():
    for target, spec in _equiv_cases():
        assert compile(spec).glom(target) == glom(target, spec)


def test_glommer_compile_matches_glommer_glom():
    glommer = Glommer()
    for target, spec in _equiv_cases():
        assert glommer.compile(spec).glom(target) == glommer.glom(target, spec)


def test_compile_matches_glom_on_error():
    compiled = compile('a.b')
    with pytest.raises(PathAccessError):
        compiled.glom({})
    with pytest.raises(PathAccessError):
        glom({}, 'a.b')


def test_compile_str_spec_preparsed():
    compiled = compile('a.b')
    assert isinstance(compiled.spec, Path)
    assert compiled.spec == Path('a', 'b')
    assert compiled.glom({'a': {'b': 'c'}}) == 'c'


def test_compile_invalid_spec_raises():
    with pytest.raises(BadSpec):
        compile(123)
    with pytest.raises(BadSpec):
        Glommer().compile(None)


def test_compiled_spec_batch_consistency():
    spec = {'double': ('n', lambda n: n * 2)}
    compiled = compile(spec)
    targets = [{'n': i} for i in range(1000)]
    assert [compiled.glom(t) for t in targets] == [glom(t, spec) for t in targets]


def test_compiled_spec_no_cached_values():
    compiled = compile(T['a'])
    assert compiled.glom({'a': 1}) == 1
    assert compiled.glom({'a': 2}) == 2


def test_compiled_spec_temp_bindings_not_cached():
    compiled = compile((S(x=T['a']), S['x']))
    assert compiled.glom({'a': 1}) == 1
    assert compiled.glom({'a': 2}) == 2

    compiled = compile((Let(v=T['a']), S['v']))
    assert compiled.glom({'a': 1}) == 1
    assert compiled.glom({'a': 2}) == 2


def test_compiled_spec_local_scope_not_cached():
    compiled = compile(S['marker'])
    assert compiled.glom({}, scope={'marker': 1}) == 1
    # the previous run's local scope must not leak into the next run
    with pytest.raises(GlomError):
        compiled.glom({})
    # and a later local scope is unaffected by the earlier one
    assert compiled.glom({}, scope={'marker': 2}) == 2


def test_compiled_spec_kwargs_passthrough():
    compiled = compile('a.b')
    assert compiled.glom({}, default='d') == 'd'
    assert compiled.glom({}, skip_exc=GlomError, default='d') == 'd'
    with pytest.raises(TypeError):
        compiled.glom({}, bogus_kwarg=True)


def test_glommer_compile_sees_later_registration():
    glommer = Glommer()
    compiled = glommer.compile('a')
    box = Box({'a': 5})
    with pytest.raises(GlomError):
        compiled.glom(box)
    glommer.register(Box, get=lambda b, k: b.items[k])
    assert compiled.glom(box) == 5


def test_glommer_compile_registry_isolation():
    glommer = Glommer()
    other = Glommer()
    compiled = glommer.compile('a')
    other.register(Box, get=lambda b, k: b.items[k])
    with pytest.raises(GlomError):
        compiled.glom(Box({'a': 5}))


def test_module_compile_sees_module_register():
    compiled = compile('a')
    register(ModuleBox, get=lambda b, k: b.items[k])
    assert compiled.glom(ModuleBox({'a': 7})) == 7


def test_compiled_spec_repr():
    assert repr(compile('a.b')) == "CompiledSpec(Path('a', 'b'))"
