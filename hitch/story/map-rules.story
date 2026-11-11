Mapping key relationship rules (Map rules):
  docs: compound/map-rules
  based on: strictyaml
  description: |
    Some constraints in a mapping are relationships *between* keys
    rather than properties of individual keys - for example, a
    config may give 'password' or 'token' but not both, and
    'partition_key' may be required when 'partitioning' is enabled.

    You can declare these mapping-level relationships with the
    'rules' parameter of Map. Rules are only checked after every
    key and value has passed its own validation, so a field which
    fails to parse never triggers a misleading combination error.
  given:
    setup: |
      from strictyaml import Map, Str, Bool, Optional, MutuallyExclusive, Requires, load
      from ensure import Ensure

      schema = Map({
          Optional("password"): Str(),
          Optional("token"): Str(),
          Optional("partitioning"): Bool(),
          Optional("partition_key"): Str(),
      }, rules=[
          MutuallyExclusive("password", "token"),
          Requires("partitioning", "partition_key"),
      ])
  variations:
    password only:
      given:
        yaml_snippet: 'password: hunter2'
      steps:
      - Run:
          code: |
            Ensure(load(yaml_snippet, schema)).equals({"password": "hunter2"})

    token only:
      given:
        yaml_snippet: 'token: abc'
      steps:
      - Run:
          code: |
            Ensure(load(yaml_snippet, schema)).equals({"token": "abc"})

    password and token together:
      given:
        yaml_snippet: |
          password: hunter2
          token: abc
      steps:
      - Run:
          code: load(yaml_snippet, schema)
          raises:
            type: strictyaml.exceptions.YAMLValidationError
            message: |-
              while parsing a mapping
              mutually exclusive keys 'password', 'token' found together
                in "<unicode string>", line 1, column 1:
                  password: hunter2
                   ^ (line: 1)

    partitioning enabled without partition key:
      given:
        yaml_snippet: 'partitioning: yes'
      steps:
      - Run:
          code: load(yaml_snippet, schema)
          raises:
            type: strictyaml.exceptions.YAMLValidationError
            message: |-
              while parsing a mapping
              key(s) 'partition_key' required when 'partitioning' is enabled
                in "<unicode string>", line 1, column 1:
                  partitioning: yes
                   ^ (line: 1)

    partitioning enabled with partition key:
      given:
        yaml_snippet: |
          partitioning: yes
          partition_key: id
      steps:
      - Run:
          code: |
            Ensure(load(yaml_snippet, schema)).equals({"partitioning": True, "partition_key": "id"})

    partitioning disabled:
      given:
        yaml_snippet: 'partitioning: no'
      steps:
      - Run:
          code: |
            Ensure(load(yaml_snippet, schema)).equals({"partitioning": False})

    invalid field does not trigger combination error:
      given:
        yaml_snippet: 'partitioning: maybe'
      steps:
      - Run:
          code: load(yaml_snippet, schema)
          raises:
            type: strictyaml.exceptions.YAMLValidationError
            message: |-
              when expecting a boolean value (one of "yes", "true", "on", "1", "y", "no", "false", "off", "0", "n")
              found arbitrary text
                in "<unicode string>", line 1, column 1:
                  partitioning: maybe
                   ^ (line: 1)

    rule referring to key not in schema:
      steps:
      - Run:
          code: |
            Map({"a": Str()}, rules=[MutuallyExclusive("a", "b")])
          raises:
            type: strictyaml.exceptions.InvalidValidatorError
            message: "MutuallyExclusive('a', 'b') refers to key(s) not in schema: 'b'"
