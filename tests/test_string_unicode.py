"""Unicode boundary regressions for string validation.

JSON Schema string length is defined in terms of Unicode code points
(RFC 8259), which on CPython is ``len(str)``. These tests pin that:

* a combining character sequence counts every code point, not the single
  user-perceived grapheme;
* a non-BMP character (for example an emoji) is one code point, not two
  UTF-16 code units and not four UTF-8 bytes;
* a ZWJ emoji sequence counts each emoji and each ZWJ separately;
* an unpaired surrogate is one Python code unit and can even be matched
  by ``pattern``, although such a string cannot be encoded as UTF-8, so
  counting bytes is not an option.

Every case is run against both the directly compiled validator and the
code produced by ``compile_to_code``; the two backends must agree.
"""

import pytest

from fastjsonschema import (
    JsonSchemaValueException,
    compile as compile_schema,
    compile_to_code,
)


# "é" expressed as base letter "e" + combining acute accent:
# one grapheme, two code points, three UTF-8 bytes.
COMBINING = 'é'
# Grinning face: one non-BMP code point, four UTF-8 bytes.
NON_BMP = '\U0001F600'
# Family: man + ZWJ + woman + ZWJ + girl:
# one grapheme, five code points, eighteen UTF-8 bytes.
ZWJ_SEQUENCE = '\U0001F468‍\U0001F469‍\U0001F467'
# Unpaired high / low surrogates: one code unit each, not UTF-8 encodable.
LONE_HIGH_SURROGATE = '\ud800'
LONE_LOW_SURROGATE = '\udfff'
SURROUNDED_SURROGATE = 'a\ud800b'


def _utf8_byte_count(value):
    try:
        return len(value.encode('utf-8'))
    except UnicodeEncodeError:
        return None


def _validation_outcome(validator, value):
    try:
        validator(value)
    except JsonSchemaValueException as exc:
        return exc.rule
    return 'valid'


def _assert_invalid(validator, value, rule):
    with pytest.raises(JsonSchemaValueException) as exc_info:
        validator(value)
    assert exc_info.value.rule == rule


# Reference values documenting exactly which unit the keywords count.
@pytest.mark.parametrize('value, code_points, utf8_bytes', [
    (COMBINING, 2, 3),
    (NON_BMP, 1, 4),
    (ZWJ_SEQUENCE, 5, 18),
    (LONE_HIGH_SURROGATE, 1, None),
    (LONE_LOW_SURROGATE, 1, None),
    (SURROUNDED_SURROGATE, 3, None),
])
def test_length_unit_reference_values(value, code_points, utf8_bytes):
    assert len(value) == code_points
    assert _utf8_byte_count(value) == utf8_bytes


@pytest.fixture(params=['compiled', 'standalone'])
def make_validator(request):
    def _make(schema):
        if request.param == 'compiled':
            return compile_schema(schema)
        namespace = {}
        exec(compile_to_code(schema), namespace)
        return namespace['validate']
    return _make


LENGTH_SCHEMA = {'type': 'string', 'minLength': 2, 'maxLength': 4}


@pytest.mark.parametrize('value', [
    pytest.param(COMBINING, id='combining-2-code-points-1-grapheme'),
    pytest.param(SURROUNDED_SURROGATE, id='surrounded-lone-surrogate-3-code-units'),
])
def test_length_keywords_accept_code_point_count(make_validator, value):
    validator = make_validator(LENGTH_SCHEMA)
    assert validator(value) == value


@pytest.mark.parametrize('value, rule', [
    # One code point: minLength fails even though the value is 4 UTF-8 bytes.
    pytest.param(NON_BMP, 'minLength', id='non-bmp-1-code-point-4-utf8-bytes'),
    # Five code points: maxLength fails even though the user sees one grapheme.
    pytest.param(ZWJ_SEQUENCE, 'maxLength', id='zwj-5-code-points-1-grapheme'),
    # One code unit: the string is not UTF-8 encodable, yet length is still 1.
    pytest.param(LONE_HIGH_SURROGATE, 'minLength', id='lone-high-surrogate'),
    pytest.param(LONE_LOW_SURROGATE, 'minLength', id='lone-low-surrogate'),
])
def test_length_keywords_reject_on_code_point_count(make_validator, value, rule):
    _assert_invalid(make_validator(LENGTH_SCHEMA), value, rule)


def test_pattern_dot_counts_combining_marks_as_code_points(make_validator):
    # One grapheme but two code points, so two dots are required.
    validator = make_validator({'type': 'string', 'pattern': r'^.{2}$'})
    assert validator(COMBINING) == COMBINING
    _assert_invalid(
        make_validator({'type': 'string', 'pattern': r'^.$'}),
        COMBINING,
        'pattern',
    )


def test_pattern_dot_does_not_split_non_bmp_character(make_validator):
    # The non-BMP character is a single code point, so one dot matches it
    # and two dots do not (it is neither two UTF-16 units nor four bytes).
    validator = make_validator({'type': 'string', 'pattern': r'^.$'})
    assert validator(NON_BMP) == NON_BMP
    _assert_invalid(
        make_validator({'type': 'string', 'pattern': r'^.{2}$'}),
        NON_BMP,
        'pattern',
    )


def test_pattern_dot_matches_every_component_of_zwj_sequence(make_validator):
    # Three emoji plus two ZWJ = five code points behind one grapheme.
    validator = make_validator({'type': 'string', 'pattern': r'^.{5}$'})
    assert validator(ZWJ_SEQUENCE) == ZWJ_SEQUENCE
    _assert_invalid(
        make_validator({'type': 'string', 'pattern': r'^.$'}),
        ZWJ_SEQUENCE,
        'pattern',
    )


@pytest.mark.parametrize('surrogate', [
    pytest.param(LONE_HIGH_SURROGATE, id='high'),
    pytest.param(LONE_LOW_SURROGATE, id='low'),
])
def test_pattern_can_match_unpaired_surrogate(make_validator, surrogate):
    schema = {'type': 'string', 'pattern': 'a[' + surrogate + ']b'}
    validator = make_validator(schema)
    value = 'a' + surrogate + 'b'
    assert validator(value) == value
    _assert_invalid(validator, 'acb', 'pattern')


def test_min_max_length_and_pattern_are_separate_duties(make_validator):
    schema = {
        'type': 'string',
        'minLength': 2,
        'maxLength': 4,
        'pattern': r'^\w+$',
    }
    validator = make_validator(schema)
    assert validator('abcd') == 'abcd'
    _assert_invalid(validator, 'a!', 'pattern')
    _assert_invalid(validator, 'abcde', 'maxLength')
    _assert_invalid(validator, 'z', 'minLength')


@pytest.mark.parametrize('value', [
    pytest.param(COMBINING, id='combining'),
    pytest.param(NON_BMP, id='non-bmp'),
    pytest.param(ZWJ_SEQUENCE, id='zwj-sequence'),
    pytest.param(LONE_HIGH_SURROGATE, id='lone-high-surrogate'),
    pytest.param(LONE_LOW_SURROGATE, id='lone-low-surrogate'),
    pytest.param(SURROUNDED_SURROGATE, id='surrounded-surrogate'),
])
def test_compiled_and_generated_code_agree(value):
    schema = {'type': 'string', 'minLength': 2, 'maxLength': 4, 'pattern': r'^.+$'}
    compiled = compile_schema(schema)
    namespace = {}
    exec(compile_to_code(schema), namespace)
    assert _validation_outcome(compiled, value) == _validation_outcome(namespace['validate'], value)
