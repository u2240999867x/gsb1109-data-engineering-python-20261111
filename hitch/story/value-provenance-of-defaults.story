Value provenance of schema defaults:
  docs: compound/optional-keys-with-defaults
  based on: strictyaml
  description: |
    Values supplied by the schema (Optional defaults) can be told apart from
    values written in the original text. Defaults nested inside other defaults
    are also reported as defaults. Default values have no source line, and
    saving can either keep only user written values or write the current
    defaults out explicitly.
  given:
    yaml_snippet: |
      a: 1
    setup: |
      from strictyaml import Map, Int, Bool, Optional, load

      schema = Map({"a": Int(), Optional("b", default=False): Bool()})
  variations:
    Original and default values can be told apart:
      steps:
      - Run: |
          yaml = load(yaml_snippet, schema)
          assert yaml["a"].is_default is False
          assert yaml["a"].value_source == "original"
          assert yaml["b"].is_default is True
          assert yaml["b"].value_source == "default"
          assert yaml.default_keys() == ["b"]
          assert yaml.explicit_keys() == ["a"]

    Default values have no line number:
      steps:
      - Run: |
          yaml = load(yaml_snippet, schema)
          assert yaml["a"].start_line == 1
          assert yaml["b"].start_line is None
          assert yaml["b"].end_line is None
          assert yaml["b"].lines() is None
          assert yaml["b"].has_line is False

    Saving keeps only values written by the user:
      steps:
      - Run:
          code: print(load(yaml_snippet, schema).as_yaml())
          will output: |-
            a: 1

    Saving can write the current defaults explicitly:
      steps:
      - Run:
          code: print(load(yaml_snippet, schema).as_yaml(include_defaults=True))
          will output: |-
            a: 1
            b: no

    Editing one user value does not write out the defaults:
      steps:
      - Run:
          code: |
            yaml = load(yaml_snippet, schema)
            yaml["a"] = 2
            print(yaml.as_yaml())
          will output: |-
            a: 2

    Editing a default writes only that default:
      steps:
      - Run:
          code: |
            yaml = load(yaml_snippet, schema)
            yaml["b"] = True
            print(yaml.as_yaml())
          will output: |-
            a: 1
            b: yes

    Nested defaults are explained:
      given:
        yaml_snippet: |
          a:
            x: 1
      setup: |
        from strictyaml import Map, Int, Bool, Optional, load

        schema = Map({
            "a": Map({"x": Int(), Optional("y", default=False): Bool()}),
            Optional("b", default=False): Bool(),
        })
      steps:
      - Run: |
          yaml = load(yaml_snippet, schema)
          assert yaml["a"]["x"].is_default is False
          assert yaml["a"]["y"].is_default is True
          assert yaml["b"].is_default is True
          provenance = yaml.provenance()
          assert provenance["children"]["a"]["children"]["y"]["source"] == "default"
          assert provenance["children"]["b"]["source"] == "default"

    Editing a nested default writes only the edited path:
      given:
        yaml_snippet: |
          a:
            x: 1
      setup: |
        from strictyaml import Map, Int, Bool, Optional, load

        schema = Map({
            "a": Map({"x": Int(), Optional("y", default=False): Bool()}),
            Optional("b", default=False): Bool(),
        })
      steps:
      - Run:
          code: |
            yaml = load(yaml_snippet, schema)
            yaml["a"]["y"] = True
            print(yaml.as_yaml())
          will output: |-
            a:
              x: 1
              y: yes

    Editing a nested default hides untouched sibling defaults:
      given:
        yaml_snippet: |
          a: 1
      setup: |
        from strictyaml import Map, Int, Optional, load

        schema = Map({
            "a": Int(),
            Optional("cfg", default={"sub": {"m": 1}}): Map({
                "sub": Map({"m": Int(), Optional("n", default=0): Int()}),
            }),
        })
      steps:
      - Run:
          code: |
            yaml = load(yaml_snippet, schema)
            yaml["cfg"]["sub"]["m"] = 9
            print(yaml.as_yaml())
          will output: |-
            a: 1
            cfg:
              sub:
                m: 9

    Editing a user value afterwards keeps the edited default and hides others:
      given:
        yaml_snippet: |
          a: 1
      setup: |
        from strictyaml import Map, Int, Optional, load

        schema = Map({
            "a": Int(),
            Optional("cfg", default={"sub": {"m": 1}}): Map({
                "sub": Map({"m": Int(), Optional("n", default=0): Int()}),
            }),
        })
      steps:
      - Run:
          code: |
            yaml = load(yaml_snippet, schema)
            yaml["cfg"]["sub"]["m"] = 9
            yaml["a"] = 2
            print(yaml.as_yaml())
          will output: |-
            a: 2
            cfg:
              sub:
                m: 9
