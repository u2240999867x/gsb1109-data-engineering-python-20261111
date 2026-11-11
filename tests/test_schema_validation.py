import pytest

from fastjsonschema import (
    JsonSchemaDefinitionException,
    JsonSchemaUnsupportedException,
    JsonSchemaValueException,
    compile,
    compile_to_code,
    validate,
)


DRAFT_04 = 'http://json-schema.org/draft-04/schema'
DRAFT_06 = 'http://json-schema.org/draft-06/schema'
DRAFT_07 = 'http://json-schema.org/draft-07/schema'
DRAFT_2019 = 'https://json-schema.org/draft/2019-09/schema'


def test_required_as_string_is_reported_before_generation():
    with pytest.raises(JsonSchemaDefinitionException) as exc:
        compile({'type': 'object', 'required': 'name'})
    assert exc.value.message == '#/required: required must be an array'


def test_invalid_schema_error_contains_json_path_of_definition():
    with pytest.raises(JsonSchemaDefinitionException) as exc:
        compile({'properties': {'foo': {'items': [{'required': 'x'}]}}})
    assert exc.value.message == '#/properties/foo/items/0/required: required must be an array'


def test_compile_to_code_also_validates_schema():
    with pytest.raises(JsonSchemaDefinitionException, match='required must be an array'):
        compile_to_code({'required': 'name'})


@pytest.mark.parametrize('definition, message', (
    ({'type': 'str'}, "type must be one of"),
    ({'type': 42}, 'type must be a string or a non-empty array of strings'),
    ({'enum': 'abc'}, 'enum must be an array'),
    ({'minimum': '1'}, 'minimum must be a number'),
    ({'multipleOf': 0}, 'multipleOf must be greater than 0'),
    ({'minLength': -1}, 'minLength must be a non-negative integer'),
    ({'minLength': '1'}, 'minLength must be a non-negative integer'),
    ({'uniqueItems': 'yes'}, 'uniqueItems must be a boolean'),
    ({'pattern': '('}, 'pattern must be a valid regular expression'),
    ({'patternProperties': {'(': {}}}, 'patternProperties keys must be valid regular expressions'),
    ({'properties': []}, 'properties must be an object'),
    ({'properties': {'a': 1}}, 'definition must be an object or a boolean'),
    ({'allOf': {}}, 'allOf must be an array'),
    ({'allOf': [1]}, 'allOf must contain only schemas'),
    ({'required': ['a', 'a']}, 'required must contain unique elements'),
    ({'required': ['a', 1]}, 'required must contain only strings'),
    ({'dependencies': {'a': 'b'}}, 'dependencies must be a schema or an array of strings'),
    ({'$ref': 5}, r'\$ref must be a string'),
))
def test_invalid_definitions(definition, message):
    with pytest.raises(JsonSchemaDefinitionException, match=message):
        compile(definition)


def test_exclusive_minimum_boolean_is_valid_only_in_draft_04():
    compile({'$schema': DRAFT_04, 'exclusiveMinimum': True})
    with pytest.raises(JsonSchemaDefinitionException, match='exclusiveMinimum must be a boolean'):
        compile({'$schema': DRAFT_04, 'exclusiveMinimum': 5})
    compile({'$schema': DRAFT_06, 'exclusiveMinimum': 5})
    with pytest.raises(JsonSchemaDefinitionException, match='exclusiveMinimum must be a number'):
        compile({'$schema': DRAFT_06, 'exclusiveMinimum': True})


def test_undeclared_schema_accepts_constructs_of_any_supported_draft():
    # Boolean exclusiveMaximum (draft-04) and items as an array (up to
    # draft-07) are historically accepted when no $schema is declared.
    validator = compile({
        'maximum': 10,
        'exclusiveMaximum': True,
        'items': [{'type': 'number'}],
    })
    assert validator([5]) == [5]


@pytest.mark.parametrize('keyword', (
    'dependentRequired', 'dependentSchemas', 'prefixItems',
    'minContains', 'maxContains', 'unevaluatedItems', 'unevaluatedProperties',
))
def test_valid_but_unsupported_keyword_is_distinguished(keyword):
    definition = {'$schema': DRAFT_2019, keyword: True}
    with pytest.raises(JsonSchemaUnsupportedException) as exc:
        compile(definition)
    assert not isinstance(exc.value, JsonSchemaDefinitionException)
    assert keyword in exc.value.message
    assert '#/{}'.format(keyword) in exc.value.message


def test_unsupported_keyword_is_reported_for_undeclared_schema_too():
    with pytest.raises(JsonSchemaUnsupportedException, match='unevaluatedProperties'):
        compile({'unevaluatedProperties': False})


def test_unsupported_keyword_in_referenced_schema_reports_its_uri():
    remote = {'$schema': DRAFT_2019, 'properties': {'a': {'dependentRequired': {'b': ['c']}}}}
    with pytest.raises(JsonSchemaUnsupportedException) as exc:
        compile({'$ref': 'sch://remote.json'}, handlers={'sch': lambda uri: remote})
    assert 'sch://remote.json#/properties/a/dependentRequired' in exc.value.message


def test_error_in_referenced_schema_reports_its_own_uri_and_path():
    remote = {'properties': {'a': {'required': 'x'}}}
    with pytest.raises(JsonSchemaDefinitionException) as exc:
        compile({'$ref': 'sch://remote.json'}, handlers={'sch': lambda uri: remote})
    assert exc.value.message == 'sch://remote.json#/properties/a/required: required must be an array'


def test_error_in_local_definition_reports_path():
    with pytest.raises(JsonSchemaDefinitionException) as exc:
        compile({'definitions': {'num': {'minimum': 'x'}}, '$ref': '#/definitions/num'})
    assert exc.value.message == '#/definitions/num/minimum: minimum must be a number'


def test_annotations_and_extension_keywords_are_not_errors():
    validator = compile({
        '$schema': DRAFT_07,
        '$comment': 'a comment',
        'title': 'a title',
        'description': 'a description',
        'default': 42,
        'examples': [1, 2],
        'readOnly': True,
        'writeOnly': False,
        'x-custom-extension': {'anything': ['goes', 1, None]},
        'anotherExtension': True,
        'type': 'integer',
    })
    assert validator(1) == 1


def test_ref_siblings_are_ignored_like_in_code_generation():
    validator = compile({
        'definitions': {'num': {'type': 'number'}},
        'allOf': [{
            '$ref': '#/definitions/num',
            # Ignored together with the rest of siblings of $ref.
            'required': 'not an array',
        }],
    })
    assert validator(1) == 1


def test_boolean_schema_is_valid():
    assert compile(True)(1) == 1
    with pytest.raises(JsonSchemaValueException):
        compile(False)(1)


def test_validate_function_also_validates_schema():
    with pytest.raises(JsonSchemaDefinitionException, match='required must be an array'):
        validate({'required': 'name'}, {})
