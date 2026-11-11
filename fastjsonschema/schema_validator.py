"""
Pre-compile validation of a schema against the rules of its declared draft.

The code generator assumes the given schema follows the meta-schema of its
declared draft. When it does not (for example when ``required`` is written
as a plain string), the failure used to surface late and deep inside the
generator as a hard to understand Python error. This module checks the
schema, including referenced documents, before any code is generated and
reports problems together with their ``uri#/json/pointer`` location.

Two kinds of problems are distinguished:

 * :any:`JsonSchemaDefinitionException` -- the schema itself is invalid
   according to the draft it declares.
 * :any:`JsonSchemaUnsupportedException` -- the schema is valid, but uses
   a keyword defined by its draft which this implementation does not
   support yet.

Annotation keywords (``$comment``, ``title``, ...) and unknown extension
keywords are always allowed, as required by the JSON Schema specification.
"""

import re
from decimal import Decimal
from urllib import parse as urlparse

from .draft04 import CodeGeneratorDraft04
from .draft06 import CodeGeneratorDraft06
from .draft07 import CodeGeneratorDraft07
from .draft2019 import CodeGeneratorDraft2019
from .exceptions import JsonSchemaDefinitionException, JsonSchemaUnsupportedException
from .ref_resolver import get_id, normalize

SIMPLE_TYPES = ('array', 'boolean', 'integer', 'null', 'number', 'object', 'string')

# Keywords the code generators handle outside of their keyword map: ``$ref``
# is dispatched before it and the rest is processed as part of a sibling
# keyword (``minimum``/``maximum``, ``items`` and ``if``).
IMPLICITLY_SUPPORTED_KEYWORDS = frozenset((
    '$ref', 'exclusiveMinimum', 'exclusiveMaximum', 'additionalItems', 'then', 'else',
))


def validate_schema_definition(definition, resolver, code_generator):
    """
    Check ``definition`` against the rules of its declared draft before
    any code is generated from it.

    Referenced documents (local and remote) are checked as well and
    problems are reported with the URI and JSON path of the place they
    are defined at.
    """
    supported_keywords = set(code_generator._json_keywords_to_function) | IMPLICITLY_SUPPORTED_KEYWORDS
    state = _ValidationState(resolver, supported_keywords)
    root_validator_class = _validator_class_for(definition, _generator_default_validator(code_generator))
    _validate_document(state, definition, resolver.get_uri(), root_validator_class)


def _validate_document(state, document, uri, default_validator_class):
    normalized = normalize(uri) if uri else ''
    if normalized in state.validated_documents or normalized in state.validating_documents:
        return
    state.validating_documents.add(normalized)
    try:
        validator_class = _validator_class_for(document, default_validator_class)
        validator_class(state, default_validator_class).walk_document(document, uri)
    finally:
        state.validating_documents.discard(normalized)
        state.validated_documents.add(normalized)


def _validator_class_for(document, default):
    if isinstance(document, dict):
        schema_version = document.get('$schema', '')
        if isinstance(schema_version, str):
            if 'draft-04' in schema_version:
                return Draft04SchemaValidator
            if 'draft-06' in schema_version:
                return Draft06SchemaValidator
            if 'draft-07' in schema_version:
                return Draft07SchemaValidator
            if 'draft/2019' in schema_version or 'draft-2019' in schema_version:
                return Draft2019SchemaValidator
    return default


def _generator_default_validator(code_generator):
    # The generator class is chosen from the root schema only, so every
    # document without its own ``$schema`` is compiled by this generator.
    if isinstance(code_generator, CodeGeneratorDraft2019):
        # Draft 2019 is the fallback when nothing is declared; historically
        # such schemas may mix constructs of any supported draft.
        return LenientSchemaValidator
    if isinstance(code_generator, CodeGeneratorDraft07):
        return Draft07SchemaValidator
    if isinstance(code_generator, CodeGeneratorDraft06):
        return Draft06SchemaValidator
    if isinstance(code_generator, CodeGeneratorDraft04):
        return Draft04SchemaValidator
    return LenientSchemaValidator


def _is_number(value):
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def _is_integer(value):
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and value.is_integer()


def format_location(uri, path):
    """
    Location of a schema (sub)definition as ``uri#/json/pointer``.
    """
    pointer = ''.join(
        '/' + str(part).replace('~', '~0').replace('/', '~1')
        for part in path
    )
    return '{}#{}'.format(uri, pointer) if uri else '#{}'.format(pointer)


class _ValidationState:
    def __init__(self, resolver, supported_keywords):
        self.resolver = resolver
        self.supported_keywords = supported_keywords
        self.validated_documents = set()
        self.validating_documents = set()


# pylint: disable=too-many-instance-attributes
class Draft04SchemaValidator:
    """
    Validate a schema against the rules of JSON schema draft-04.
    """

    draft_name = 'draft-04'

    # Keywords defined by the draft which do not affect validation.
    annotation_keywords = frozenset((
        '$schema', 'id', 'title', 'description', 'default',
    ))
    # Keywords defined by the draft which affect validation. Presence of
    # such a keyword not implemented by the code generator means the schema
    # is valid, but this implementation cannot compile it.
    feature_keywords = frozenset((
        '$ref',
        'type', 'enum', 'allOf', 'anyOf', 'oneOf', 'not',
        'minLength', 'maxLength', 'pattern', 'format',
        'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum', 'multipleOf',
        'minItems', 'maxItems', 'uniqueItems', 'items', 'additionalItems',
        'minProperties', 'maxProperties', 'required', 'dependencies',
        'properties', 'patternProperties', 'additionalProperties',
    ))
    # Keywords holding one subschema, an array of subschemas or a map
    # of subschemas respectively.
    schema_keywords = frozenset(('not', 'additionalItems', 'additionalProperties'))
    schema_array_keywords = frozenset(('allOf', 'anyOf', 'oneOf'))
    schema_map_keywords = frozenset(('properties', 'patternProperties', 'definitions'))

    keyword_checks = {
        '$schema': '_check_string',
        'id': '_check_string',
        '$ref': '_check_string',
        'title': '_check_string',
        'description': '_check_string',
        'format': '_check_string',
        'type': '_check_type',
        'enum': '_check_enum',
        'multipleOf': '_check_multiple_of',
        'minimum': '_check_number',
        'maximum': '_check_number',
        'exclusiveMinimum': '_check_boolean',
        'exclusiveMaximum': '_check_boolean',
        'minLength': '_check_non_negative_integer',
        'maxLength': '_check_non_negative_integer',
        'minItems': '_check_non_negative_integer',
        'maxItems': '_check_non_negative_integer',
        'minProperties': '_check_non_negative_integer',
        'maxProperties': '_check_non_negative_integer',
        'uniqueItems': '_check_boolean',
        'pattern': '_check_pattern',
        'required': '_check_required',
        'items': '_check_items',
        'properties': '_check_schema_map',
        'patternProperties': '_check_pattern_properties',
        'definitions': '_check_schema_map',
        'dependencies': '_check_dependencies',
        'allOf': '_check_schema_array',
        'anyOf': '_check_schema_array',
        'oneOf': '_check_schema_array',
        'not': '_check_schema',
        'additionalItems': '_check_schema',
        'additionalProperties': '_check_schema',
    }

    def __init__(self, state, root_validator_class):
        self._state = state
        self._root_validator_class = root_validator_class

    def walk_document(self, document, uri):
        # Iterative to stay clear of the recursion limit on deeply nested
        # schemas (the resolver already bounds their depth).
        stack = [(document, [], uri)]
        while stack:
            node, path, node_uri = stack.pop()
            if isinstance(node, bool):
                continue
            if not isinstance(node, dict):
                raise JsonSchemaDefinitionException(
                    '{}: definition must be an object or a boolean'.format(format_location(node_uri, path))
                )
            node_id = get_id(node)
            if isinstance(node_id, str) and node_id:
                node_uri = normalize(urlparse.urljoin(node_uri, node_id))
            ref = node.get('$ref')
            if isinstance(ref, str):
                # A ``$ref`` overrides all its sibling keywords, so they are
                # not checked; only the referenced document is. Definition
                # containers are still walked, references can point into them.
                self._validate_referenced_document(ref, node_uri)
                stack.extend(self._definition_containers(node, path, node_uri))
                continue
            self._check_keywords(node, path, node_uri)
            stack.extend(self._subschemas(node, path, node_uri))

    def _validate_referenced_document(self, ref, uri):
        resolver = self._state.resolver
        with resolver.in_scope(uri):
            with resolver.resolving(ref):
                document_uri = resolver.base_uri
                document = resolver.schema
        _validate_document(self._state, document, document_uri, self._root_validator_class)

    def _check_keywords(self, node, path, uri):
        for keyword, value in node.items():
            if keyword in self.feature_keywords and keyword not in self._state.supported_keywords:
                raise JsonSchemaUnsupportedException(
                    "{}: '{}' is a valid {} keyword, but it is not supported by this implementation".format(
                        format_location(uri, path + [keyword]), keyword, self.draft_name,
                    )
                )
            check_name = self.keyword_checks.get(keyword)
            if check_name is not None:
                getattr(self, check_name)(keyword, value, path + [keyword], uri)

    def _subschemas(self, node, path, uri):
        subschemas = []
        for keyword in self.schema_keywords:
            value = node.get(keyword)
            if isinstance(value, (dict, bool)):
                subschemas.append((value, path + [keyword], uri))
        for keyword in self.schema_array_keywords:
            value = node.get(keyword)
            if isinstance(value, list):
                for index, item in enumerate(value):
                    if isinstance(item, (dict, bool)):
                        subschemas.append((item, path + [keyword, index], uri))
        for keyword in self.schema_map_keywords:
            value = node.get(keyword)
            if isinstance(value, dict):
                for key, item in value.items():
                    if isinstance(item, (dict, bool)):
                        subschemas.append((item, path + [keyword, key], uri))
        items = node.get('items')
        if isinstance(items, (dict, bool)):
            subschemas.append((items, path + ['items'], uri))
        elif isinstance(items, list):
            for index, item in enumerate(items):
                if isinstance(item, (dict, bool)):
                    subschemas.append((item, path + ['items', index], uri))
        dependencies = node.get('dependencies')
        if isinstance(dependencies, dict):
            for key, item in dependencies.items():
                if isinstance(item, (dict, bool)):
                    subschemas.append((item, path + ['dependencies', key], uri))
        return subschemas

    def _definition_containers(self, node, path, uri):
        subschemas = []
        for keyword in self.schema_map_keywords & {'definitions', '$defs'}:
            value = node.get(keyword)
            if isinstance(value, dict):
                for key, item in value.items():
                    if isinstance(item, (dict, bool)):
                        subschemas.append((item, path + [keyword, key], uri))
        return subschemas

    def _error(self, path, uri, message):
        raise JsonSchemaDefinitionException('{}: {}'.format(format_location(uri, path), message))

    def _check_string(self, keyword, value, path, uri):
        if not isinstance(value, str):
            self._error(path, uri, '{} must be a string'.format(keyword))

    def _check_array(self, keyword, value, path, uri):
        if not isinstance(value, list):
            self._error(path, uri, '{} must be an array'.format(keyword))

    def _check_boolean(self, keyword, value, path, uri):
        if not isinstance(value, bool):
            self._error(path, uri, '{} must be a boolean'.format(keyword))

    def _check_number(self, keyword, value, path, uri):
        if not _is_number(value):
            self._error(path, uri, '{} must be a number'.format(keyword))

    def _check_non_negative_integer(self, keyword, value, path, uri):
        if not _is_integer(value) or value < 0:
            self._error(path, uri, '{} must be a non-negative integer'.format(keyword))

    def _check_multiple_of(self, keyword, value, path, uri):
        if not _is_number(value):
            self._error(path, uri, 'multipleOf must be a number')
        if value <= 0:
            self._error(path, uri, 'multipleOf must be greater than 0')

    def _check_type(self, keyword, value, path, uri):
        if isinstance(value, str):
            types = [value]
        elif isinstance(value, list) and value:
            types = value
        else:
            self._error(path, uri, 'type must be a string or a non-empty array of strings')
        for type_name in types:
            if not isinstance(type_name, str) or type_name not in SIMPLE_TYPES:
                self._error(path, uri, "type must be one of '{}'".format("', '".join(SIMPLE_TYPES)))
        if len(set(types)) != len(types):
            self._error(path, uri, 'type must contain unique items')

    def _check_enum(self, keyword, value, path, uri):
        if not isinstance(value, list):
            self._error(path, uri, 'enum must be an array')

    def _check_pattern(self, keyword, value, path, uri):
        if not isinstance(value, str):
            self._error(path, uri, 'pattern must be a string')
        try:
            re.compile(value)
        except re.error:
            self._error(path, uri, 'pattern must be a valid regular expression')

    def _check_required(self, keyword, value, path, uri):
        if not isinstance(value, list):
            self._error(path, uri, 'required must be an array')
        if not all(isinstance(item, str) for item in value):
            self._error(path, uri, 'required must contain only strings')
        if len(set(value)) != len(value):
            self._error(path, uri, 'required must contain unique elements')

    def _check_schema(self, keyword, value, path, uri):
        if not isinstance(value, (dict, bool)):
            self._error(path, uri, '{} must be a schema (object or boolean)'.format(keyword))

    def _check_items(self, keyword, value, path, uri):
        if isinstance(value, (dict, bool)):
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                if not isinstance(item, (dict, bool)):
                    self._error(path + [index], uri, 'items must contain only schemas')
            return
        self._error(path, uri, 'items must be a schema or an array of schemas')

    def _check_schema_map(self, keyword, value, path, uri):
        if not isinstance(value, dict):
            self._error(path, uri, '{} must be an object'.format(keyword))
        for key, subschema in value.items():
            if not isinstance(subschema, (dict, bool)):
                self._error(path + [key], uri, 'definition must be an object or a boolean')

    def _check_pattern_properties(self, keyword, value, path, uri):
        self._check_schema_map(keyword, value, path, uri)
        for pattern in value:
            if not isinstance(pattern, str):
                self._error(path, uri, 'patternProperties keys must be strings')
            try:
                re.compile(pattern)
            except re.error:
                self._error(path, uri, 'patternProperties keys must be valid regular expressions')

    def _check_dependencies(self, keyword, value, path, uri):
        if not isinstance(value, dict):
            self._error(path, uri, 'dependencies must be an object')
        for key, dependency in value.items():
            if isinstance(dependency, (dict, bool)):
                continue
            if not isinstance(dependency, list):
                self._error(path + [key], uri, 'dependencies must be a schema or an array of strings')
            if not all(isinstance(item, str) for item in dependency):
                self._error(path + [key], uri, 'dependencies must contain only strings')
            if len(set(dependency)) != len(dependency):
                self._error(path + [key], uri, 'dependencies must contain unique elements')

    def _check_schema_array(self, keyword, value, path, uri):
        if not isinstance(value, list):
            self._error(path, uri, '{} must be an array'.format(keyword))
        for index, item in enumerate(value):
            if not isinstance(item, (dict, bool)):
                self._error(path + [index], uri, '{} must contain only schemas'.format(keyword))


class Draft06SchemaValidator(Draft04SchemaValidator):
    """
    Validate a schema against the rules of JSON schema draft-06.
    """

    draft_name = 'draft-06'

    annotation_keywords = Draft04SchemaValidator.annotation_keywords | {'$id', 'examples'}
    feature_keywords = Draft04SchemaValidator.feature_keywords | {'const', 'contains', 'propertyNames'}
    schema_keywords = Draft04SchemaValidator.schema_keywords | {'contains', 'propertyNames'}
    keyword_checks = dict(Draft04SchemaValidator.keyword_checks, **{
        '$id': '_check_string',
        'examples': '_check_array',
        'exclusiveMinimum': '_check_number',
        'exclusiveMaximum': '_check_number',
        'contains': '_check_schema',
        'propertyNames': '_check_schema',
    })


class Draft07SchemaValidator(Draft06SchemaValidator):
    """
    Validate a schema against the rules of JSON schema draft-07.
    """

    draft_name = 'draft-07'

    annotation_keywords = Draft06SchemaValidator.annotation_keywords | {'$comment', 'readOnly', 'writeOnly'}
    feature_keywords = Draft06SchemaValidator.feature_keywords | {
        'if', 'then', 'else', 'contentEncoding', 'contentMediaType',
    }
    schema_keywords = Draft06SchemaValidator.schema_keywords | {'if', 'then', 'else'}
    keyword_checks = dict(Draft06SchemaValidator.keyword_checks, **{
        '$comment': '_check_string',
        'readOnly': '_check_boolean',
        'writeOnly': '_check_boolean',
        'contentEncoding': '_check_string',
        'contentMediaType': '_check_string',
        'if': '_check_schema',
        'then': '_check_schema',
        'else': '_check_schema',
    })


class Draft2019SchemaValidator(Draft07SchemaValidator):
    """
    Validate a schema against the rules of JSON schema draft 2019-09.
    """

    draft_name = 'draft-2019-09'

    annotation_keywords = Draft07SchemaValidator.annotation_keywords | {'deprecated'}
    feature_keywords = Draft07SchemaValidator.feature_keywords | {
        '$anchor', '$recursiveAnchor', '$recursiveRef', '$vocabulary',
        'dependentRequired', 'dependentSchemas', 'prefixItems',
        'minContains', 'maxContains',
        'unevaluatedItems', 'unevaluatedProperties', 'contentSchema',
    }
    schema_keywords = Draft07SchemaValidator.schema_keywords | {
        'unevaluatedItems', 'unevaluatedProperties', 'contentSchema',
    }
    schema_array_keywords = Draft07SchemaValidator.schema_array_keywords | {'prefixItems'}
    schema_map_keywords = Draft07SchemaValidator.schema_map_keywords | {'$defs', 'dependentSchemas'}
    keyword_checks = dict(Draft07SchemaValidator.keyword_checks, **{
        'deprecated': '_check_boolean',
        '$defs': '_check_schema_map',
    })


class LenientSchemaValidator(Draft2019SchemaValidator):
    """
    Rules for schemas without a declared ``$schema``. They are compiled by
    the newest code generator, but historically this library accepts
    constructs of any supported draft in that case (for example boolean
    ``exclusiveMinimum`` from draft-04 or ``items`` as an array), so the
    checks accept the union of all supported drafts.
    """

    keyword_checks = dict(Draft2019SchemaValidator.keyword_checks, **{
        'exclusiveMinimum': '_check_boolean_or_number',
        'exclusiveMaximum': '_check_boolean_or_number',
    })

    def _check_boolean_or_number(self, keyword, value, path, uri):
        if not isinstance(value, bool) and not _is_number(value):
            self._error(path, uri, '{} must be a boolean or a number'.format(keyword))
