---
title: Load multi-document YAML streams
---


Deployment configuration often concatenates several YAML documents into
one file using the `---` document separator. `load_all` reads every
document in such a stream and lets the caller assign a schema and a
label to each document independently.

Unlike `load`, which still only reads a single document, `load_all` never
changes the behavior of the ordinary loaders. Whether a failed document
aborts reading or is skipped is always an explicit choice via
`continue_on_errors`.

Example yaml_snippet:

```yaml
name: app
---
count: 4

```


```python
from strictyaml import load_all, Map, Str, Int

```



Assign a schema and a label to each document:


```python
stream = load_all(
    yaml_snippet,
    [Map({"name": Str()}), Map({"count": Int()})],
    labels=["service", "metrics"],
)
for document in stream:
    print(document.index, document.label, document.yaml.data)

```

A single validator can instead be applied to every document, and a
per-document label is generated automatically when `labels` is omitted:

```python
stream = load_all(yaml_snippet, Map({"a": Int()}), label="deploy.yml")
# labels become "deploy.yml (document 1)", "deploy.yml (document 2)", ...
```

Each `YAMLDocument` exposes the parsed `yaml`, its zero-based `index`,
its `label`, and the `start_line` / `end_line` it occupies in the
original, un-split stream.

Lines that look like `---` inside multiline strings are content, not
separators:

```yaml
description: |
  ---
  still content
---
a: 1
```

### Errors point at the document and its original location

Every error carries `document_index` and `document_label`, and its
line/column numbers refer to the original YAML stream rather than to the
document in isolation.

```python
strictyaml.exceptions.YAMLValidationError:
when expecting an integer
found arbitrary text
  in "<unicode string> (document 2)", line 3, column 1:
    a: notint
     ^ (line: 3)
```

### Choosing whether one failure stops everything

By default the first failure aborts reading and raises the original
error. Pass `continue_on_errors=True` to keep going: validation and
construction failures (such as duplicate keys) are collected in
`stream.failures`, while successfully parsed documents remain in
`stream.documents`.

```python
stream = load_all(
    yaml_snippet,
    Map({"a": Int()}),
    continue_on_errors=True,
)
stream.is_valid          # False
stream.documents         # the documents that parsed and validated
stream.failures          # DocumentFailure objects with .index, .label, .error
```

Errors raised while scanning or parsing (malformed syntax, disallowed
tokens such as flow mappings) corrupt the stream state, so they always
stop reading even when `continue_on_errors` is enabled.

### Ordinary load is unchanged

`load` and `dirty_load` still read a single document and still raise if a
stream contains more than one document — multi-document support is only
available through the explicit `load_all` entry point.






!!! note "Executable specification"

    Documentation automatically generated from 
    <a href="https://github.com/crdoconnor/strictyaml/blob/master/hitch/story/multi-document-load.story">multi-document-load.story
    storytests.
