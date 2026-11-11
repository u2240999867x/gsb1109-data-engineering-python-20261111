Pure-Data Specs for Configuration
==================================

The regular glom API is a Python API: specs are built from Python objects,
including callables, lambdas, :data:`~glom.T` expressions, and imported
specifier types. That is ideal for application code, but it makes glom specs
unsuitable to hand directly to non-developers. A mapping maintained in a
config file should never be able to run arbitrary Python.

:mod:`glom.spec_data` provides a data-only dialect for exactly this case.
Specs are composed entirely of JSON/YAML-compatible data -- mappings,
lists, strings, numbers, booleans, and null. The only executable behavior
available comes from operations that the calling application has explicitly
registered, and the whole config is validated **before it runs**, so a
reference to an unknown operation fails at load time with its full config
location.

A quick example::

    >>> from glom import DataSpecRegistry, load_data_spec
    >>> registry = DataSpecRegistry()
    >>> registry.register_op('strip', str.strip)
    >>> config = {
    ...     'name': {'op': 'strip', 'args': [{'path': ['raw_name']}]},
    ...     'kind': {'if': {'path': ['is_vip']},
    ...              'then': {'value': 'vip'},
    ...              'else': {'value': 'standard'}},
    ... }
    >>> spec = load_data_spec(config, registry=registry)
    >>> spec.glom({'raw_name': '  acme ', 'is_vip': True})
    {'name': 'acme', 'kind': 'vip'}

The result of :func:`~glom.load_data_spec` is an ordinary
:class:`~glom.Spec`, so the compiled config composes with the rest of the
Python glom API.

Registering Operations
----------------------

Config authors never supply callables. The application owns a
:class:`~glom.DataSpecRegistry` and registers each allowed operation by
name:

.. autoclass:: glom.DataSpecRegistry
   :members:

A registry starts empty and is isolated: an operation registered on one
registry is invisible to every other registry. For simple applications that
want a process-wide default, :func:`~glom.register_data_op` registers on the
shared module-level registry used when ``registry`` is omitted.

Config Node Reference
---------------------

Every node in a data spec is one of the forms below.

``{"path": ["a", 0, "b"]}``
    Value access. Segments are mapping keys (strings) or sequence indices
    (integers). An empty list, ``{"path": []}``, addresses the whole
    current target -- the data-only equivalent of :data:`~glom.T` -- which
    is commonly used to pass the target to an operation.

``{"value": <literal>}``
    A literal value, returned as-is. The literal must itself be pure data;
    nested dicts and lists are allowed.

``{"op": "name", "args": [...], "kwargs": {...}}``
    Call a registered operation. ``args`` is an optional list of argument
    specs, evaluated positionally; ``kwargs`` is an optional mapping of
    keyword name to argument spec. Both are omitted for zero-argument
    operations. Referencing a name that was never registered raises
    :class:`~glom.spec_data.DataSpecError` at load time.

``{"coalesce": [spec, spec, ...]}``
    Try each sub-spec in order and use the first that succeeds, mirroring
    :class:`~glom.Coalesce`.

``{"if": cond, "then": spec, "else": spec}``
    A conditional. ``cond`` is evaluated for truthiness; ``else`` is
    optional, and when omitted a false condition yields
    :data:`~glom.SKIP`, dropping the field or list item.

``{"pipe": [step, step, ...]}``
    Run steps in sequence, threading each result into the next, mirroring
    :class:`~glom.Pipe`.

``[spec]``
    A list with exactly one element spec applies that spec to every item
    of the target list.

Any other mapping is a **field mapping**: each string key names an output
field and each value is a nested spec evaluated against the target. To
declare a field whose name collides with a reserved node key (such as
``"path"`` or ``"if"``), wrap it explicitly as
``{"map": {field_name: spec}}``.

Bare strings are deliberately *not* interpreted as paths or expressions:
there is no dotted-path syntax and no source-string evaluation. Access
always uses an explicit ``path`` node, and a literal string always uses
``value``.

Loading from JSON, YAML, and Files
----------------------------------

.. autofunction:: glom.load_data_spec
.. autofunction:: glom.loads_data_spec
.. autofunction:: glom.load_data_spec_file

YAML is parsed with ``safe_load``, so YAML tags that construct Python
objects (for example ``!!python/object/apply``) are rejected rather than
executed. Parse failures and structural problems are both reported as
:class:`~glom.spec_data.DataSpecError`.

Errors Point at the Config
--------------------------

Validation happens up front, before any target data is touched. Errors
carry a JSONPath-style location rooted at ``$``, making it clear exactly
which part of the config is at fault::

    >>> load_data_spec({'outer': [{'op': 'rm_rf'}]}, registry=registry)
    Traceback (most recent call last):
    ...
    glom.spec_data.DataSpecError: at $.outer[0]: unregistered operation 'rm_rf'; ...
Security Boundary
-----------------

The data dialect guarantees that:

- only JSON/YAML-compatible data is accepted -- callables, source strings,
  module references, and arbitrary Python objects are rejected;
- the only callable invoked is one the application registered itself;
- no imports happen while loading or compiling a config;
- unknown operations and malformed nodes fail at load time, not mid-run.

The Python spec API is unchanged and remains fully available to trusted
application code.

.. autoclass:: glom.spec_data.DataSpecError
