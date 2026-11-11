import operator
import collections
from functools import reduce
from collections.abc import Mapping

__all__ = ('merge', 'merge_with', 'valmap', 'keymap', 'itemmap',
           'valfilter', 'keyfilter', 'itemfilter',
           'assoc', 'dissoc', 'assoc_in', 'update_in', 'get_in',
           'assoc_path', 'update_path')


def _get_factory(f, kwargs):
    factory = kwargs.pop('factory', dict)
    if kwargs:
        raise TypeError("{}() got an unexpected keyword argument "
                        "'{}'".format(f.__name__, kwargs.popitem()[0]))
    return factory


def merge(*dicts, **kwargs):
    """ Merge a collection of dictionaries

    >>> merge({1: 'one'}, {2: 'two'})
    {1: 'one', 2: 'two'}

    Later dictionaries have precedence

    >>> merge({1: 2, 3: 4}, {3: 3, 4: 4})
    {1: 2, 3: 3, 4: 4}

    See Also:
        merge_with
    """
    if len(dicts) == 1 and not isinstance(dicts[0], Mapping):
        dicts = dicts[0]
    factory = _get_factory(merge, kwargs)

    rv = factory()
    for d in dicts:
        rv.update(d)
    return rv


def merge_with(func, *dicts, **kwargs):
    """ Merge dictionaries and apply function to combined values

    A key may occur in more than one dict, and all values mapped from the key
    will be passed to the function as a list, such as func([val1, val2, ...]).

    >>> merge_with(sum, {1: 1, 2: 2}, {1: 10, 2: 20})
    {1: 11, 2: 22}

    >>> merge_with(first, {1: 1, 2: 2}, {2: 20, 3: 30})  # doctest: +SKIP
    {1: 1, 2: 2, 3: 30}

    See Also:
        merge
    """
    if len(dicts) == 1 and not isinstance(dicts[0], Mapping):
        dicts = dicts[0]
    factory = _get_factory(merge_with, kwargs)

    values = collections.defaultdict(lambda: [].append)
    for d in dicts:
        for k, v in d.items():
            values[k](v)

    result = factory()
    for k, v in values.items():
        result[k] = func(v.__self__)
    return result


def valmap(func, d, factory=dict):
    """ Apply function to values of dictionary

    >>> bills = {"Alice": [20, 15, 30], "Bob": [10, 35]}
    >>> valmap(sum, bills)  # doctest: +SKIP
    {'Alice': 65, 'Bob': 45}

    See Also:
        keymap
        itemmap
    """
    rv = factory()
    rv.update(zip(d.keys(), map(func, d.values())))
    return rv


def keymap(func, d, factory=dict):
    """ Apply function to keys of dictionary

    >>> bills = {"Alice": [20, 15, 30], "Bob": [10, 35]}
    >>> keymap(str.lower, bills)  # doctest: +SKIP
    {'alice': [20, 15, 30], 'bob': [10, 35]}

    See Also:
        valmap
        itemmap
    """
    rv = factory()
    rv.update(zip(map(func, d.keys()), d.values()))
    return rv


def itemmap(func, d, factory=dict):
    """ Apply function to items of dictionary

    >>> accountids = {"Alice": 10, "Bob": 20}
    >>> itemmap(reversed, accountids)  # doctest: +SKIP
    {10: "Alice", 20: "Bob"}

    See Also:
        keymap
        valmap
    """
    rv = factory()
    rv.update(map(func, d.items()))
    return rv


def valfilter(predicate, d, factory=dict):
    """ Filter items in dictionary by value

    >>> iseven = lambda x: x % 2 == 0
    >>> d = {1: 2, 2: 3, 3: 4, 4: 5}
    >>> valfilter(iseven, d)
    {1: 2, 3: 4}

    See Also:
        keyfilter
        itemfilter
        valmap
    """
    rv = factory()
    for k, v in d.items():
        if predicate(v):
            rv[k] = v
    return rv


def keyfilter(predicate, d, factory=dict):
    """ Filter items in dictionary by key

    >>> iseven = lambda x: x % 2 == 0
    >>> d = {1: 2, 2: 3, 3: 4, 4: 5}
    >>> keyfilter(iseven, d)
    {2: 3, 4: 5}

    See Also:
        valfilter
        itemfilter
        keymap
    """
    rv = factory()
    for k, v in d.items():
        if predicate(k):
            rv[k] = v
    return rv


def itemfilter(predicate, d, factory=dict):
    """ Filter items in dictionary by item

    >>> def isvalid(item):
    ...     k, v = item
    ...     return k % 2 == 0 and v < 4

    >>> d = {1: 2, 2: 3, 3: 4, 4: 5}
    >>> itemfilter(isvalid, d)
    {2: 3}

    See Also:
        keyfilter
        valfilter
        itemmap
    """
    rv = factory()
    for item in d.items():
        if predicate(item):
            k, v = item
            rv[k] = v
    return rv


def assoc(d, key, value, factory=dict):
    """ Return a new dict with new key value pair

    New dict has d[key] set to value. Does not modify the initial dictionary.

    >>> assoc({'x': 1}, 'x', 2)
    {'x': 2}
    >>> assoc({'x': 1}, 'y', 3)   # doctest: +SKIP
    {'x': 1, 'y': 3}
    """
    d2 = factory()
    d2.update(d)
    d2[key] = value
    return d2


def dissoc(d, *keys, **kwargs):
    """ Return a new dict with the given key(s) removed.

    New dict has d[key] deleted for each supplied key.
    Does not modify the initial dictionary.

    >>> dissoc({'x': 1, 'y': 2}, 'y')
    {'x': 1}
    >>> dissoc({'x': 1, 'y': 2}, 'y', 'x')
    {}
    >>> dissoc({'x': 1}, 'y') # Ignores missing keys
    {'x': 1}
    """
    factory = _get_factory(dissoc, kwargs)
    d2 = factory()

    if len(keys) < len(d) * .6:
        d2.update(d)
        for key in keys:
            if key in d2:
                del d2[key]
    else:
        remaining = set(d)
        remaining.difference_update(keys)
        for k in remaining:
            d2[k] = d[k]
    return d2


def assoc_in(d, keys, value, factory=dict):
    """ Return a new dict with new, potentially nested, key value pair

    >>> purchase = {'name': 'Alice',
    ...             'order': {'items': ['Apple', 'Orange'],
    ...                       'costs': [0.50, 1.25]},
    ...             'credit card': '5555-1234-1234-1234'}
    >>> assoc_in(purchase, ['order', 'costs'], [0.25, 1.00]) # doctest: +SKIP
    {'credit card': '5555-1234-1234-1234',
     'name': 'Alice',
     'order': {'costs': [0.25, 1.00], 'items': ['Apple', 'Orange']}}
    """
    return update_in(d, keys, lambda x: value, value, factory)


def update_in(d, keys, func, default=None, factory=dict):
    """ Update value in a (potentially) nested dictionary

    inputs:
    d - dictionary on which to operate
    keys - list or tuple giving the location of the value to be changed in d
    func - function to operate on that value

    If keys == [k0,..,kX] and d[k0]..[kX] == v, update_in returns a copy of the
    original dictionary with v replaced by func(v), but does not mutate the
    original dictionary.

    If k0 is not a key in d, update_in creates nested dictionaries to the depth
    specified by the keys, with the innermost value set to func(default).

    >>> inc = lambda x: x + 1
    >>> update_in({'a': 0}, ['a'], inc)
    {'a': 1}

    >>> transaction = {'name': 'Alice',
    ...                'purchase': {'items': ['Apple', 'Orange'],
    ...                             'costs': [0.50, 1.25]},
    ...                'credit card': '5555-1234-1234-1234'}
    >>> update_in(transaction, ['purchase', 'costs'], sum) # doctest: +SKIP
    {'credit card': '5555-1234-1234-1234',
     'name': 'Alice',
     'purchase': {'costs': 1.75, 'items': ['Apple', 'Orange']}}

    >>> # updating a value when k0 is not in d
    >>> update_in({}, [1, 2, 3], str, default="bar")
    {1: {2: {3: 'bar'}}}
    >>> update_in({1: 'foo'}, [2, 3, 4], inc, 0)
    {1: 'foo', 2: {3: {4: 1}}}
    """
    ks = iter(keys)
    k = next(ks)

    rv = inner = factory()
    rv.update(d)

    for key in ks:
        if k in d:
            d = d[k]
            dtemp = factory()
            dtemp.update(d)
        else:
            d = dtemp = factory()

        inner[k] = inner = dtemp
        k = key

    if k in d:
        inner[k] = func(d[k])
    else:
        inner[k] = func(default)
    return rv


def get_in(keys, coll, default=None, no_default=False):
    """ Returns coll[i0][i1]...[iX] where [i0, i1, ..., iX]==keys.

    If coll[i0][i1]...[iX] cannot be found, returns ``default``, unless
    ``no_default`` is specified, then it raises KeyError or IndexError.

    ``get_in`` is a generalization of ``operator.getitem`` for nested data
    structures such as dictionaries and lists.

    >>> transaction = {'name': 'Alice',
    ...                'purchase': {'items': ['Apple', 'Orange'],
    ...                             'costs': [0.50, 1.25]},
    ...                'credit card': '5555-1234-1234-1234'}
    >>> get_in(['purchase', 'items', 0], transaction)
    'Apple'
    >>> get_in(['name'], transaction)
    'Alice'
    >>> get_in(['purchase', 'total'], transaction)
    >>> get_in(['purchase', 'items', 'apple'], transaction)
    >>> get_in(['purchase', 'items', 10], transaction)
    >>> get_in(['purchase', 'total'], transaction, 0)
    0
    >>> get_in(['y'], {}, no_default=True)
    Traceback (most recent call last):
        ...
    KeyError: 'y'

    See Also:
        itertoolz.get
        operator.getitem
    """
    try:
        return reduce(operator.getitem, keys, coll)
    except (KeyError, IndexError, TypeError):
        if no_default:
            raise
        return default


def _is_path_container(node):
    return isinstance(node, (Mapping, list, tuple))


def _path_getitem(node, key):
    if isinstance(node, (Mapping, list, tuple)):
        return node[key]
    raise TypeError("cannot follow path key %r into object of type %r"
                    % (key, type(node).__name__))


def _path_assoc(node, key, value):
    """ Return a shallow copy of ``node`` with ``node[key]`` set to ``value``
    """
    if isinstance(node, Mapping):
        new = node.copy() if hasattr(node, 'copy') else dict(node)
        new[key] = value
        return new
    if isinstance(node, list):
        new = list(node)
        new[key] = value
        return new
    if isinstance(node, tuple):
        if hasattr(node, '_fields'):  # namedtuple
            return node._replace(**{node._fields[key]: value})
        new = list(node)
        new[key] = value
        return tuple(new)
    raise TypeError("cannot update object of type %r"
                    % type(node).__name__)


def _walk_path(coll, path):
    """ Return the list of nodes along ``path``, starting with ``coll``

    Raises KeyError, IndexError, or TypeError if the path does not exist in
    ``coll``, and ValueError if a container occurs twice along the path
    (i.e. the path leads through a cycle).
    """
    nodes = [coll]
    seen = set()
    node = coll
    if _is_path_container(node):
        seen.add(id(node))
    for key in path:
        node = _path_getitem(node, key)
        if _is_path_container(node):
            if id(node) in seen:
                raise ValueError("cycle detected along path; "
                                 "refusing to update")
            seen.add(id(node))
        nodes.append(node)
    return nodes


def _container_parents(root):
    """ Map id(child) -> list of ids of containers directly holding it

    Every container (mapping, list, tuple) reachable from ``root`` is
    scanned.  Cycles are followed only once, so this always terminates.
    """
    parents = {}
    visited = set()
    stack = [root]
    while stack:
        node = stack.pop()
        if not _is_path_container(node) or id(node) in visited:
            continue
        visited.add(id(node))
        children = node.values() if isinstance(node, Mapping) else node
        for child in children:
            parents.setdefault(id(child), []).append(id(node))
            if _is_path_container(child):
                stack.append(child)
    return parents


def update_path(coll, path, func, alias='break'):
    """ Update the value at an explicit path in nested dicts and lists

    inputs:
    coll - nested combination of dicts, lists, tuples, and other values
    path - list or tuple of keys/indices locating the value to update
    func - function to apply to the value found at the path
    alias - policy for shared (aliased) containers, one of 'break',
        'preserve', or 'error'; see below

    Returns a new structure with the value at ``path`` replaced by
    ``func(value)``.  The input is never mutated, and containers not on the
    path are shared with the input rather than copied, so unchanged sub-trees
    (however large) cost nothing.

    Paths may freely mix mappings and sequences:

    >>> doc = {'user': {'name': 'Alice'}, 'meta': {'flags': ['x', 'y']}}
    >>> update_path(doc, ['meta', 'flags', 1], str.upper)
    {'user': {'name': 'Alice'}, 'meta': {'flags': ['x', 'Y']}}

    The original is untouched and unchanged parts are shared:

    >>> new = update_path(doc, ['meta', 'flags', 1], str.upper)
    >>> doc['meta']['flags']
    ['x', 'y']
    >>> new['user'] is doc['user']
    True

    When two paths refer to the same container, the ``alias`` policy decides
    what an update means:

    - ``alias='break'`` (default): only the given path changes; other
      references keep pointing to the old object.
    - ``alias='preserve'``: every reference to a container on the path is
      updated, so existing aliasing is kept intact.
    - ``alias='error'``: raise ValueError if any container on the path is
      referenced from more than one place.

    >>> shared = {'n': 0}
    >>> d = {'a': shared, 'b': shared}
    >>> update_path(d, ['a', 'n'], lambda x: x + 1)
    {'a': {'n': 1}, 'b': {'n': 0}}
    >>> update_path(d, ['a', 'n'], lambda x: x + 1, alias='preserve')
    {'a': {'n': 1}, 'b': {'n': 1}}
    >>> update_path(d, ['a', 'n'], lambda x: x + 1, alias='error')
    Traceback (most recent call last):
        ...
    ValueError: alias='error': container along path is shared

    The path must already exist; missing keys, out-of-range indices, and
    cycles raise KeyError, IndexError, or ValueError respectively, always
    leaving the input unchanged:

    >>> update_path({'a': [1, 2]}, ['a', 5], str)
    Traceback (most recent call last):
        ...
    IndexError: list index out of range

    See Also:
        assoc_path
        update_in
        get_in
    """
    if alias not in ('break', 'preserve', 'error'):
        raise ValueError("alias must be one of 'break', 'preserve', or "
                         "'error', got %r" % (alias,))
    path = tuple(path)
    nodes = _walk_path(coll, path)
    new = func(nodes[-1])

    if alias == 'break':
        for node, key in zip(reversed(nodes[:-1]), reversed(path)):
            new = _path_assoc(node, key, new)
        return new

    target_id = id(nodes[-1])
    parents = _container_parents(coll)

    if alias == 'error':
        path_ids = {id(node) for node in nodes if _is_path_container(node)}
        for node_id in path_ids:
            expected = 0 if node_id == id(coll) else 1
            if len(parents.get(node_id, [])) > expected:
                raise ValueError("alias='error': container along path is "
                                 "shared")
        for node, key in zip(reversed(nodes[:-1]), reversed(path)):
            new = _path_assoc(node, key, new)
        return new

    # alias == 'preserve': rebuild every container that can reach the
    # replaced node, memoizing so shared containers stay shared.
    dirty = set()
    stack = [target_id]
    while stack:
        for parent_id in parents.get(stack.pop(), []):
            if parent_id not in dirty:
                dirty.add(parent_id)
                stack.append(parent_id)

    memo = {}
    in_progress = set()

    def rebuild(node):
        node_id = id(node)
        if node_id == target_id:
            return new
        if node_id not in dirty:
            return node
        if node_id in memo:
            return memo[node_id]
        if node_id in in_progress:
            raise ValueError("cannot preserve aliases through a cycle")
        in_progress.add(node_id)
        if isinstance(node, Mapping):
            rebuilt = node.copy() if hasattr(node, 'copy') else dict(node)
            for key in node:
                rebuilt[key] = rebuild(node[key])
        elif isinstance(node, list):
            rebuilt = [rebuild(child) for child in node]
        elif hasattr(node, '_fields'):  # namedtuple
            rebuilt = type(node)(*(rebuild(child) for child in node))
        else:
            rebuilt = tuple(rebuild(child) for child in node)
        in_progress.discard(node_id)
        memo[node_id] = rebuilt
        return rebuilt

    return rebuild(coll)


def assoc_path(coll, path, value, alias='break'):
    """ Return a new structure with ``value`` set at an explicit path

    Like ``update_path``, but sets the value directly instead of applying a
    function.  Dicts and lists may be mixed along the path, the input is
    never mutated, and unchanged sub-trees are shared with the result.

    >>> purchase = {'name': 'Alice',
    ...             'order': {'items': ['Apple', 'Orange'],
    ...                       'costs': [0.50, 1.25]}}
    >>> assoc_path(purchase, ['order', 'costs', 0], 0.25)  # doctest: +SKIP
    {'name': 'Alice',
     'order': {'items': ['Apple', 'Orange'], 'costs': [0.25, 1.25]}}

    See ``update_path`` for the meaning of the ``alias`` policy.

    See Also:
        update_path
        assoc_in
    """
    return update_path(coll, path, lambda _: value, alias=alias)
