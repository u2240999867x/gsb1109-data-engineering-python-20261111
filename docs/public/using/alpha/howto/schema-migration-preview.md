---
title: Preview a schema migration
---


When a schema is upgraded, the same YAML text can be interpreted
differently. A common example is a value like `yes` which used to be
validated as text (`Str()`) but is now validated as a boolean (`Bool()`).

`preview_migration` interprets one YAML string under both the old and the
new schema and reports the differences without changing the YAML object
or the source file:

* **Type changes** - positions present in the text whose parsed type
  changed (e.g. the text `"yes"` becoming `True`).
* **Default changes** - positions missing from the text whose effective
  default differs between the schemas, including defaults that were added
  or removed.
* **Rejected positions** - positions the new schema refuses to accept.

Documents that cannot be loaded are reported separately from documents
which load but change meaning.

Example yaml_snippet:

```yaml
sources:
  - name: orders
    enabled: yes
  - name: clicks
```



```python
from strictyaml import Map, Seq, Str, Bool, Optional, preview_migration

old_schema = Map({
    "sources": Seq(Map({
        "name": Str(),
        Optional("enabled", default="yes"): Str(),
    })),
})

new_schema = Map({
    "sources": Seq(Map({
        "name": Str(),
        Optional("enabled", default=True): Bool(),
    })),
})

preview = preview_migration(yaml_snippet, old_schema, new_schema)

```



Inspect the preview:


```python
preview.loadable
True

[(change.path_string, change.old_type, change.new_type)
 for change in preview.type_changes]
[('sources.[0].enabled', 'str', 'bool')]

[(change.path_string, change.old_default, change.new_default)
 for change in preview.default_changes]
[('sources.[1].enabled', 'yes', True)]

preview.rejected
[]

```



The preview only reads the text, so existing YAML objects and files are
untouched. Once the preview has been reviewed, the new interpretation is
applied by re-parsing the original text. This raises if the new schema
rejects any position:


```python
new_document = preview.apply()
new_document["sources"][0]["enabled"].data
True

```
