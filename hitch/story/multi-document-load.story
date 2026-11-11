Load multi-document YAML streams:
  docs: howto/load-multi-document-yaml
  based on: strictyaml
  description: |
    Deployment configuration often concatenates several YAML documents into
    one file using the '---' document separator. load_all reads every
    document in such a stream and lets the caller assign a schema and a
    label to each document independently.

    Unlike load, which still only reads a single document, load_all never
    changes the behavior of the ordinary loaders. Whether a failed document
    aborts reading or is skipped is always an explicit choice via
    continue_on_errors.
  given:
    setup: |
      from strictyaml import (
          load,
          load_all,
          Map,
          Str,
          Int,
          YAMLValidationError,
          MultiDocumentError,
      )
  variations:
    Assign a schema and a label to each document:
      given:
        yaml_snippet: |
          name: app
          ---
          count: 4
      steps:
      - Run:
          code: |
            stream = load_all(
                yaml_snippet,
                [Map({"name": Str()}), Map({"count": Int()})],
                labels=["service", "metrics"],
            )
            for document in stream:
                if "name" in document.yaml:
                    print(document.index, document.label, document.yaml["name"].data)
                else:
                    print(document.index, document.label, document.yaml["count"].data)
          will output: |-
            0 service app
            1 metrics 4

    Apply one schema to every document:
      given:
        yaml_snippet: |
          a: 1
          ---
          a: 2
      steps:
      - Run:
          code: |
            stream = load_all(
                yaml_snippet,
                Map({"a": Int()}),
                label="deploy.yml",
            )
            for document in stream:
                print(document.label, document.yaml["a"].data)
          will output: |-
            deploy.yml (document 1) 1
            deploy.yml (document 2) 2

    Separator-like lines inside multiline strings stay content:
      given:
        yaml_snippet: |
          description: |
            ---
            still content
          ---
          a: 1
      steps:
      - Run:
          code: |
            stream = load_all(
                yaml_snippet,
                [Map({"description": Str()}), Map({"a": Int()})],
            )
            assert len(stream) == 2
            assert stream[0].yaml["description"].data == "---\nstill content\n"
            assert stream[0].start_line == 1
            assert stream[0].end_line == 4
            assert stream[1].yaml["a"].data == 1

    Read without schemas:
      given:
        yaml_snippet: |
          a: 1
          ---
          - x
          - y
      steps:
      - Run:
          code: |
            stream = load_all(yaml_snippet)
            assert stream.is_valid
            assert stream[0].yaml.data == {"a": "1"}
            assert stream[1].yaml.data == ["x", "y"]

    Errors name the document and the original stream location:
      given:
        yaml_snippet: |
          a: 1
          ---
          a: notint
          ---
          a: 3
      steps:
      - Run:
          code: |
            load_all(yaml_snippet, Map({"a": Int()}))
          raises:
            type: strictyaml.exceptions.YAMLValidationError
            message: |-
              when expecting an integer
              found arbitrary text
                in "<unicode string> (document 2)", line 3, column 1:
                  a: notint
                   ^ (line: 3)

    The first failure aborts by default:
      given:
        yaml_snippet: |
          a: 1
          ---
          a: notint
          ---
          a: 3
      steps:
      - Run:
          code: |
            try:
                load_all(yaml_snippet, Map({"a": Int()}))
            except YAMLValidationError as error:
                print(error.document_index)
                print(error.document_label)
          will output: |-
            1
            <unicode string> (document 2)

    Continue past recoverable validation failures:
      given:
        yaml_snippet: |
          a: 1
          ---
          a: notint
          ---
          a: 3
      steps:
      - Run:
          code: |
            stream = load_all(
                yaml_snippet,
                Map({"a": Int()}),
                continue_on_errors=True,
            )
            assert not stream.is_valid
            assert [document.yaml["a"].data for document in stream.documents] == [1, 3]
            assert len(stream.failures) == 1
            assert stream.failures[0].index == 1
            assert stream.failures[0].label == "<unicode string> (document 2)"
            assert type(stream.failures[0].error).__name__ == "YAMLValidationError"

    Construction failures such as duplicate keys are recoverable too:
      given:
        yaml_snippet: |
          a: 1
          ---
          b: 1
          b: 2
          ---
          a: 3
      steps:
      - Run:
          code: |
            stream = load_all(
                yaml_snippet,
                Map({"a": Int()}),
                continue_on_errors=True,
            )
            assert [document.yaml["a"].data for document in stream.documents] == [1, 3]
            assert len(stream.failures) == 1
            assert stream.failures[0].index == 1
            print(type(stream.failures[0].error).__name__)
          will output: DuplicateKeysDisallowed

    Scan and parse failures always stop the stream:
      given:
        yaml_snippet: |
          a: 1
          ---
          b: [1, 2]
          ---
          a: 3
      steps:
      - Run:
          code: |
            stream = load_all(
                yaml_snippet,
                Map({"a": Int()}),
                continue_on_errors=True,
            )
            assert [document.yaml["a"].data for document in stream.documents] == [1]
            assert len(stream.failures) == 1
            assert stream.failures[0].index == 1
            assert type(stream.failures[0].error).__name__ == "FlowMappingDisallowed"

    Scan and parse failures still raise by default:
      given:
        yaml_snippet: |
          a: 1
          ---
          b: [1, 2]
      steps:
      - Run:
          code: |
            load_all(yaml_snippet, Map({"a": Int()}))
          raises:
            type: strictyaml.exceptions.FlowMappingDisallowed
            message: |-
              While scanning
                in "<unicode string> (document 2)", line 3, column 4:
                  b: [1, 2]
                     ^ (line: 3)
              Found ugly disallowed JSONesque flow mapping (surround with ' and ' to make text appear literally)
                in "<unicode string> (document 2)", line 3, column 5:
                  b: [1, 2]
                      ^ (line: 3)

    Schema counts must match document counts:
      given:
        yaml_snippet: |
          a: 1
      steps:
      - Run:
          code: |
            load_all(yaml_snippet, [Map({"a": Int()}), Map({"a": Int()})])
          raises:
            type: strictyaml.exceptions.MultiDocumentError
            message: |-
              2 schema(s) were supplied for a stream containing 1 document(s). When passing a list of schemas there must be exactly one (Validator or None) per document.

    Label counts must match document counts:
      given:
        yaml_snippet: |
          a: 1
      steps:
      - Run:
          code: |
            load_all(yaml_snippet, labels=["first", "second"])
          raises:
            type: strictyaml.exceptions.MultiDocumentError
            message: |-
              2 label(s) were supplied for a stream containing 1 document(s). When passing labels there must be exactly one per document.

    Ordinary load still only accepts one document:
      given:
        yaml_snippet: |
          a: 1
          ---
          b: 2
      steps:
      - Run:
          code: |
            load(yaml_snippet)
          raises:
            type: strictyaml.ruamel.composer.ComposerError
            message: |-
              expected a single document in the stream
                in "<unicode string>", line 1, column 1:
                  a: 1
                  ^ (line: 1)
              but found another document
                in "<unicode string>", line 2, column 1:
                  ---
                  ^ (line: 2)

    An empty stream produces no documents:
      given:
        yaml_snippet: ''
      steps:
      - Run:
          code: |
            stream = load_all(yaml_snippet)
            assert len(stream) == 0
            assert stream.is_valid
            assert stream.failures == []
