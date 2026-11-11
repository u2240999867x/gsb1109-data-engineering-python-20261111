---
title: Optional keys with defaults (Map/Optional)
---

!!! warning "Experimental"

    This feature is in alpha. The API may change on a minor version increment.




Example yaml_snippet:

```yaml
a: 1

```


```python
from strictyaml import Map, Int, Str, Bool, EmptyNone, Optional, load, as_document
from collections import OrderedDict
from ensure import Ensure

schema = Map({"a": Int(), Optional("b", default=False): Bool(), })

```



When parsed the result will include the optional value:


```python
Ensure(load(yaml_snippet, schema).data).equals(OrderedDict([("a", 1), ("b", False)]))

```




If parsed and then output to YAML again the default data won't be there:


```python
print(load(yaml_snippet, schema).as_yaml())
```

```yaml
a: 1
```




When default data is output to YAML it is removed:


```python
print(as_document({"a": 1, "b": False}, schema).as_yaml())

```

```yaml
a: 1
```




When you want a key to stay and default to None:


```python
schema = Map({"a": Int(), Optional("b", default=None, drop_if_none=False): EmptyNone() | Bool(), })
Ensure(load(yaml_snippet, schema).data).equals(OrderedDict([("a", 1), ("b", None)]))

```







## Telling defaults apart from user-written values

Every value knows where it came from. `is_default` is `True` when the value
was supplied by the schema, and `value_source` is either `"original"` (written
in the source text or explicitly set later) or `"default"`.

```python
yaml = load(yaml_snippet, schema)
assert yaml["a"].is_default is False
assert yaml["a"].value_source == "original"
assert yaml["b"].is_default is True
assert yaml["b"].value_source == "default"
```

`default_keys()` and `explicit_keys()` list the immediate keys of a mapping
which came from the schema and from the source text respectively.

Defaults nested inside other defaults are reported as defaults too.
`provenance()` returns a nested description of where every value came from:

```python
schema = Map({
    "a": Map({"x": Int(), Optional("y", default=False): Bool()}),
    Optional("b", default=False): Bool(),
})
yaml = load(yaml_snippet, schema)
assert yaml["a"]["y"].is_default is True
assert yaml["b"].is_default is True
assert yaml.provenance()["children"]["a"]["children"]["y"]["source"] == "default"
```

A value which was supplied by a default has no line in the original document.
`start_line`, `end_line`, `lines()`, `lines_before()` and `lines_after()`
return `None` for such values, and `has_line` is `False`.

## Saving with or without defaults

`as_yaml()` keeps only values written by the user. Schema defaults are not
written to the file, even after editing an unrelated user value:

```python
yaml = load(yaml_snippet, schema)
yaml["a"] = 2
print(yaml.as_yaml())
```

```yaml
a: 2
```

Pass `include_defaults=True` to write the current defaults out explicitly:

```python
print(yaml.as_yaml(include_defaults=True))
```

```yaml
a: 2
b: no
```

Editing a default value writes just that value; untouched sibling defaults
stay hidden:

```python
yaml = load(yaml_snippet, schema)
yaml["b"] = True
print(yaml.as_yaml())
```

```yaml
a: 1
b: yes
```


!!! note "Executable specification"

    Documentation automatically generated from 
    <a href="https://github.com/crdoconnor/strictyaml/blob/master/hitch/story/optional-with-defaults.story">optional-with-defaults.story
    storytests.