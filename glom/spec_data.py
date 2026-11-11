"""Pure-data glom specs for untrusted, configuration-driven use cases.

The regular glom API accepts Python objects as specs, including callables,
lambdas, ``T`` expressions, and imported specifier types. That makes glom
very powerful for application code, but it means a config file editable by
operations staff can effectively embed arbitrary Python.

This module provides a data-only dialect: specs are built exclusively from
JSON/YAML-compatible data (dicts, lists, strings, numbers, booleans, and
``None``). The only executable behavior available is through operations that
the calling application has explicitly registered, with declared argument
forms. Source strings, callables, imports, and module references are never
accepted, and malformed configs -- including references to unregistered
operations -- raise :class:`DataSpecError` at load time, with the full
config location, before any target data is processed.

Example::

    from glom.spec_data import DataSpecRegistry, load_data_spec

    registry = DataSpecRegistry()
    registry.register_op('strip', str.strip)
    registry.register_op('join', lambda sep, parts: sep.join(parts))

    config = {
        'name': {'op': 'strip', 'args': [{'path': ['raw_name']}]},
        'tags': {'op': 'join',
                 'args': [{'value': ','}, {'path': ['raw_tags']}]},
        'kind': {'if': {'path': ['is_vip']},
                 'then': {'value': 'vip'},
                 'else': {'value': 'standard'}},
    }
    spec = load_data_spec(config, registry=registry)
    spec.glom({'raw_name': '  acme ', 'raw_tags': ['a', 'b'],
               'is_vip': True})

The resulting object is an ordinary :class:`~glom.Spec`, so the existing
Python spec API in :mod:`glom` is unchanged and remains available to
application code.
"""

import json

from glom.core import (glom, Spec, Path, Val, Pipe, Coalesce, Invoke,
                       SKIP, _MISSING, BadSpec)

__all__ = ['DataSpecError', 'DataSpecRegistry', 'load_data_spec',
           'loads_data_spec', 'load_data_spec_file', 'register_data_op']

# dict keys reserved by the data dialect; a single-key dict with one of
# these is interpreted as the corresponding node, not a field mapping.
# A single-field mapping whose field name collides with a reserved key can
# be written explicitly as {"map": {field_name: spec}}.
_OP_KEY = 'op'
_OP_NODE_KEYS = ('op', 'args', 'kwargs')
_IF_KEYS = ('if', 'then', 'else')


class DataSpecError(BadSpec):
    """Raised when a pure-data glom config is malformed, uses a forbidden
    construct (such as a source string or callable), or references an
    operation that was never registered.

    Errors are raised at load time -- before the spec runs against any
    target -- and the message includes the full location within the config.
    """


def _format_loc(loc):
    text = '$'
    for part in loc:
        if isinstance(part, int) and not isinstance(part, bool):
            text += '[%d]' % part
        elif isinstance(part, str) and part.isidentifier():
            text += '.' + part
        else:
            text += '[%r]' % (part,)
    return text


def _is_data_literal(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return True
    if isinstance(value, (list, tuple)):
        return all(_is_data_literal(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_data_literal(val)
                   for key, val in value.items())
    return False


class _Conditional:
    """Internal specifier backing the data-dialect ``if`` node.

    The condition is evaluated for truthiness; a missing ``else`` branch
    yields :data:`~glom.SKIP`, which drops the field or list item.
    """
    def __init__(self, condition, then, otherwise):
        self.condition = condition
        self.then = then
        self.otherwise = otherwise

    def glomit(self, target, scope):
        if scope[glom](target, self.condition, scope):
            return scope[glom](target, self.then, scope)
        if self.otherwise is _MISSING:
            return SKIP
        return scope[glom](target, self.otherwise, scope)

    def __repr__(self):
        return 'DataSpecIf(%r, then=%r, else=%r)' % (
            self.condition, self.then, self.otherwise)


class DataSpecRegistry:
    """Holds the operations that a pure-data config is allowed to invoke.

    Each operation is a name mapped to a Python callable supplied by the
    calling application. Config authors may only reference registered names
    and pass arguments in the declared data forms; they cannot supply
    callables, source strings, or imports themselves.

    A registry is isolated: operations registered on one instance are not
    visible on another, and a fresh registry starts with no operations.
    """
    def __init__(self):
        self._ops = {}

    @property
    def registered_ops(self):
        """A sorted tuple of operation names currently registered."""
        return tuple(sorted(self._ops))

    def register_op(self, name, func):
        """Register *func* under *name* for use in data specs.

        Args:
           name (str): The name configs use in an ``{"op": name}`` node.
           func (callable): The implementation, owned by the application.
             Re-registering a name replaces the previous implementation.
        """
        if not isinstance(name, str) or not name:
            raise TypeError('expected operation name to be a non-empty string,'
                            ' not: %r' % (name,))
        if not callable(func):
            raise TypeError('expected func for operation %r to be callable,'
                            ' not: %r' % (name, func))
        self._ops[name] = func

    def compile_op(self, node, loc):
        name = node[_OP_KEY]
        if not isinstance(name, str):
            raise DataSpecError(
                'at %s: "op" must be a string naming a registered operation,'
                ' not: %r' % (_format_loc(loc), name))
        func = self._ops.get(name)
        if func is None:
            registered = ', '.join(self.registered_ops) or '(none registered)'
            raise DataSpecError(
                'at %s: unregistered operation %r; register it with'
                ' DataSpecRegistry.register_op() first. Registered operations:'
                ' %s' % (_format_loc(loc), name, registered))

        unknown = sorted(set(node) - set(_OP_NODE_KEYS))
        if unknown:
            raise DataSpecError(
                'at %s: operation node for %r has unknown key(s) %r; allowed'
                ' keys are %r'
                % (_format_loc(loc), name, unknown, list(_OP_NODE_KEYS)))

        args_node = node.get('args', [])
        if not isinstance(args_node, list):
            raise DataSpecError(
                'at %s: "args" for operation %r must be a list of argument'
                ' specs, not: %r' % (_format_loc(loc + ['args']), name,
                                     type(args_node).__name__))
        kwargs_node = node.get('kwargs', {})
        if not isinstance(kwargs_node, dict) or not all(
                isinstance(key, str) for key in kwargs_node):
            raise DataSpecError(
                'at %s: "kwargs" for operation %r must be an object mapping'
                ' keyword names to argument specs, not: %r'
                % (_format_loc(loc + ['kwargs']), name,
                   type(kwargs_node).__name__))

        invoke = Invoke(func)
        for index, arg_node in enumerate(args_node):
            invoke = invoke.specs(
                _compile(arg_node, self, loc + ['args', index]))
        if kwargs_node:
            invoke = invoke.specs(**{
                key: _compile(value, self, loc + ['kwargs', key])
                for key, value in kwargs_node.items()})
        return invoke


_DEFAULT_REGISTRY = DataSpecRegistry()


def register_data_op(name, func):
    """Module-level convenience for registering an operation on the shared
    default registry. Prefer an explicit :class:`DataSpecRegistry` instance
    when isolation between components matters.
    """
    _DEFAULT_REGISTRY.register_op(name, func)


def _compile_literal(value, loc):
    if not _is_data_literal(value):
        raise DataSpecError(
            'at %s: only JSON/YAML-compatible data is allowed (dicts, lists,'
            ' strings, numbers, booleans, null); got %r which is not'
            ' pure data -- callables and source strings are not accepted'
            % (_format_loc(loc), type(value).__name__))
    return Val(value)


def _compile_path(node, loc):
    if not isinstance(node, list):
        raise DataSpecError(
            'at %s: "path" must be a list of string/int segments, not: %r'
            % (_format_loc(loc), node))
    if not node:
        # an empty path addresses the whole current target, the data-only
        # equivalent of glom's T, e.g. when passing the target to an op
        return Path()
    segments = []
    for index, segment in enumerate(node):
        if isinstance(segment, bool) or not isinstance(segment, (str, int)):
            raise DataSpecError(
                'at %s: path segments must be strings or integers, not: %r'
                % (_format_loc(loc + [index]), type(segment).__name__))
        segments.append(segment)
    return Path(*segments)


def _compile_coalesce(node, registry, loc):
    if not isinstance(node, list) or not node:
        raise DataSpecError(
            'at %s: "coalesce" must be a non-empty list of fallback specs,'
            ' not: %r' % (_format_loc(loc), node))
    return Coalesce(*[_compile(item, registry, loc + [index])
                      for index, item in enumerate(node)])


def _compile_if(node, registry, loc):
    if not isinstance(node, dict) or not all(key in _IF_KEYS for key in node):
        raise DataSpecError(
            'at %s: "if" node must be an object with only keys %s, not: %r'
            % (_format_loc(loc), ', '.join(map(repr, _IF_KEYS)), node))
    if 'if' not in node or 'then' not in node:
        raise DataSpecError(
            'at %s: conditional node requires both "if" (condition) and'
            ' "then" branches; got keys: %r'
            % (_format_loc(loc), sorted(node)))
    condition = _compile(node['if'], registry, loc + ['if'])
    then = _compile(node['then'], registry, loc + ['then'])
    otherwise = (_compile(node['else'], registry, loc + ['else'])
                 if 'else' in node else _MISSING)
    return _Conditional(condition, then, otherwise)


def _compile_pipe(node, registry, loc):
    if not isinstance(node, list) or not node:
        raise DataSpecError(
            'at %s: "pipe" must be a non-empty list of steps, not: %r'
            % (_format_loc(loc), node))
    return Pipe(*[_compile(item, registry, loc + [index])
                  for index, item in enumerate(node)])


def _compile_map(node, registry, loc):
    if not isinstance(node, dict):
        raise DataSpecError(
            'at %s: "map" must be an object of field name to spec, not: %r'
            % (_format_loc(loc), node))
    if not all(isinstance(key, str) for key in node):
        raise DataSpecError(
            'at %s: mapping keys must be strings, not: %r'
            % (_format_loc(loc), node))
    return {key: _compile(value, registry, loc + [key])
            for key, value in node.items()}


def _compile(node, registry, loc):
    if isinstance(node, dict):
        if not all(isinstance(key, str) for key in node):
            raise DataSpecError(
                'at %s: mapping keys must be strings, not: %r'
                % (_format_loc(loc), node))
        if _OP_KEY in node:
            return registry.compile_op(node, loc)
        # a flat conditional node: {"if": cond, "then": ..., "else": ...}
        # strict key validation happens inside _compile_if, so typos such
        # as an extra sibling key are reported rather than treated as a map
        if 'if' in node and 'then' in node:
            return _compile_if(node, registry, loc)
        if len(node) == 1:
            key = next(iter(node))
            if key == 'value':
                return _compile_literal(node['value'], loc + ['value'])
            if key == 'path':
                return _compile_path(node['path'], loc + ['path'])
            if key == 'coalesce':
                return _compile_coalesce(node['coalesce'], registry,
                                         loc + ['coalesce'])
            if key == 'if':
                return _compile_if(node['if'], registry, loc + ['if'])
            if key == 'pipe':
                return _compile_pipe(node['pipe'], registry, loc + ['pipe'])
            if key == 'map':
                return _compile_map(node['map'], registry, loc + ['map'])
        # otherwise: a field mapping, every value being a nested spec
        return _compile_map(node, registry, loc)
    if isinstance(node, list):
        if not node:
            raise DataSpecError(
                'at %s: empty list spec; list nodes require one element spec'
                % _format_loc(loc))
        return [_compile(item, registry, loc + [index])
                for index, item in enumerate(node)]
    if isinstance(node, str):
        raise DataSpecError(
            'at %s: bare string %r is not accepted: source strings and'
            ' dotted paths are not interpreted here. Use {"path": [...]}'
            ' for access or {"value": %r} for a literal'
            % (_format_loc(loc), node, node))
    if node is None or isinstance(node, (bool, int, float)):
        return _compile_literal(node, loc)
    raise DataSpecError(
        'at %s: unsupported spec node of type %r: %r -- expected a mapping,'
        ' list, or registered node'
        % (_format_loc(loc), type(node).__name__, node))


def load_data_spec(data, registry=None):
    """Compile pure-data *data* (e.g. parsed JSON/YAML) into a glom
    :class:`~glom.Spec`.

    The whole config is validated and compiled up front. Any forbidden
    construct or unregistered operation raises :class:`DataSpecError` with
    its full config location before the spec is returned.

    Args:
       data: A dict, list, or scalar made only of JSON/YAML-compatible
         types.
       registry (DataSpecRegistry): The registry of operations the config
         is allowed to call. Defaults to the shared module-level registry.
    """
    if registry is None:
        registry = _DEFAULT_REGISTRY
    elif not isinstance(registry, DataSpecRegistry):
        raise TypeError('expected registry to be a DataSpecRegistry instance,'
                        ' not: %r' % (registry,))
    return Spec(_compile(data, registry, []))


def loads_data_spec(text, format='json', registry=None):
    """Load a data spec from *text* in ``'json'`` or ``'yaml'`` format.

    YAML loading uses ``safe_load``, so YAML tags that construct Python
    objects are rejected. The parsed data is then compiled with
    :func:`load_data_spec`.
    """
    if format == 'json':
        try:
            data = json.loads(text)
        except ValueError as e:
            raise DataSpecError('failed to parse JSON config: %s' % e)
    elif format in ('yaml', 'yml'):
        try:
            import yaml
        except ImportError:
            raise DataSpecError(
                'PyYAML is required to load YAML data specs; install the'
                ' "yaml" extra or parse the config yourself and use'
                ' load_data_spec()')
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise DataSpecError('failed to parse YAML config: %s' % e)
    else:
        raise DataSpecError(
            'unsupported data spec format %r; expected "json" or "yaml"'
            % (format,))
    return load_data_spec(data, registry=registry)


def load_data_spec_file(file_obj, format=None, registry=None):
    """Read a data spec from an open text file object.

    The format is inferred from a ``.json``/``.yaml``/``.yml`` ``name``
    attribute when *format* is not given.
    """
    text = file_obj.read()
    if format is None:
        name = getattr(file_obj, 'name', '')
        lowered = name.lower()
        if lowered.endswith(('.yaml', '.yml')):
            format = 'yaml'
        elif lowered.endswith('.json'):
            format = 'json'
        else:
            raise DataSpecError(
                'could not infer data spec format from file name %r; pass'
                ' format="json" or format="yaml"' % (name,))
    return loads_data_spec(text, format=format, registry=registry)
