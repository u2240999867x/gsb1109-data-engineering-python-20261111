import ast

import pytest

from fastjsonschema import (
    JsonSchemaDefinitionException,
    JsonSchemaRegexException,
    JsonSchemaValueException,
    RegexStrategy,
    REGEX_STRATEGY_REGEX,
    REGEX_STRATEGY_STDLIB,
    compile,
    compile_to_code,
    validate,
)

CATASTROPHIC_PATTERN = r'(a|aa)+$'
CATASTROPHIC_VALUE = 'a' * 40 + '!'


def test_default_strategy_is_stdlib():
    validator = compile({'type': 'string', 'pattern': '^[a-z]+$'})
    assert validator.regex_strategy == RegexStrategy(REGEX_STRATEGY_STDLIB)
    assert validator.regex_strategy.name == REGEX_STRATEGY_STDLIB
    assert validator.regex_strategy.timeout is None


def test_regex_strategy_attribute_and_default_timeout():
    validator = compile({'type': 'string'}, regex_strategy=REGEX_STRATEGY_REGEX)
    assert validator.regex_strategy == RegexStrategy(REGEX_STRATEGY_REGEX, 0.1)
    custom = compile({'type': 'string'}, regex_strategy=REGEX_STRATEGY_REGEX, regex_timeout=0.02)
    assert custom.regex_strategy.timeout == 0.02


def test_regex_strategy_object_can_be_passed():
    strategy = RegexStrategy(REGEX_STRATEGY_REGEX, 0.03)
    validator = compile({'type': 'string'}, regex_strategy=strategy)
    assert validator.regex_strategy is strategy or validator.regex_strategy == strategy


def test_invalid_strategy_and_timeout_rejected():
    with pytest.raises(JsonSchemaDefinitionException):
        compile({'type': 'string'}, regex_strategy='bogus')
    for bad_timeout in (0, -1, True):
        with pytest.raises(JsonSchemaDefinitionException):
            compile({'type': 'string'}, regex_strategy=REGEX_STRATEGY_REGEX, regex_timeout=bad_timeout)
    with pytest.raises(JsonSchemaDefinitionException):
        RegexStrategy(REGEX_STRATEGY_STDLIB, 1.0)


def test_conflicting_strategy_object_and_timeout():
    with pytest.raises(JsonSchemaDefinitionException):
        compile(
            {'type': 'string'},
            regex_strategy=RegexStrategy(REGEX_STRATEGY_REGEX, 0.05),
            regex_timeout=0.2,
        )


def test_ordinary_mismatch_is_still_value_exception():
    validator = compile(
        {'type': 'string', 'pattern': '^[a-z]+$'},
        regex_strategy=REGEX_STRATEGY_REGEX,
        regex_timeout=0.05,
    )
    with pytest.raises(JsonSchemaValueException) as exc:
        validator('abc1')
    assert exc.value.rule == 'pattern'
    assert not isinstance(exc.value, JsonSchemaRegexException)


def test_pattern_timeout_raises_regex_exception_not_mismatch():
    validator = compile(
        {'type': 'string', 'pattern': CATASTROPHIC_PATTERN},
        regex_strategy=REGEX_STRATEGY_REGEX,
        regex_timeout=0.05,
    )
    with pytest.raises(JsonSchemaRegexException) as exc:
        validator(CATASTROPHIC_VALUE)
    assert not isinstance(exc.value, JsonSchemaValueException)
    assert exc.value.pattern == CATASTROPHIC_PATTERN
    assert 'terminated' in exc.value.message


def test_pattern_timeout_with_fast_fail_off_still_raises_regex_exception():
    validator = compile(
        {'type': 'string', 'pattern': CATASTROPHIC_PATTERN},
        regex_strategy=REGEX_STRATEGY_REGEX,
        regex_timeout=0.05,
        fast_fail=False,
    )
    with pytest.raises(JsonSchemaRegexException):
        validator(CATASTROPHIC_VALUE)


def test_pattern_properties_timeout_raises_regex_exception():
    validator = compile(
        {'type': 'object', 'patternProperties': {CATASTROPHIC_PATTERN: {'type': 'integer'}}},
        regex_strategy=REGEX_STRATEGY_REGEX,
        regex_timeout=0.05,
    )
    with pytest.raises(JsonSchemaRegexException) as exc:
        validator({CATASTROPHIC_VALUE: 1})
    assert exc.value.pattern == CATASTROPHIC_PATTERN
    # Non-catastrophic keys keep working and mismatches stay value exceptions.
    assert validator({'aa': 1}) == {'aa': 1}
    with pytest.raises(JsonSchemaValueException):
        validator({'aa': 'not-an-int'})


def test_timeout_propagates_through_any_of_trial():
    validator = compile(
        {
            'anyOf': [
                {'type': 'string', 'pattern': CATASTROPHIC_PATTERN},
                {'type': 'integer'},
            ]
        },
        regex_strategy=REGEX_STRATEGY_REGEX,
        regex_timeout=0.05,
    )
    # The terminated pattern must not be swallowed as a failed anyOf branch.
    with pytest.raises(JsonSchemaRegexException):
        validator(CATASTROPHIC_VALUE)


def test_unsupported_syntax_is_definition_exception_for_stdlib():
    with pytest.raises(JsonSchemaDefinitionException, match='regex strategy'):
        compile({'type': 'string', 'pattern': r'\p{L}+'}, regex_strategy=REGEX_STRATEGY_STDLIB)


def test_regex_strategy_supports_regex_only_syntax():
    validator = compile(
        {'type': 'string', 'pattern': r'\p{L}+'},
        regex_strategy=REGEX_STRATEGY_REGEX,
    )
    assert validator('äbc') == 'äbc'
    with pytest.raises(JsonSchemaValueException):
        validator('123')


def test_validate_function_uses_strategy():
    with pytest.raises(JsonSchemaRegexException):
        validate(
            {'type': 'string', 'pattern': CATASTROPHIC_PATTERN},
            CATASTROPHIC_VALUE,
            regex_strategy=REGEX_STRATEGY_REGEX,
            regex_timeout=0.05,
        )


def test_wrapped_validator_with_formats_keeps_strategy():
    validator = compile(
        {'type': 'string', 'format': 'hostname'},
        formats={'hostname': r'[a-z]+\.example\.com'},
        regex_strategy=REGEX_STRATEGY_REGEX,
        regex_timeout=0.05,
    )
    assert validator.regex_strategy.name == REGEX_STRATEGY_REGEX
    assert validator('host.example.com') == 'host.example.com'


def test_formats_keep_stdlib_engine_under_regex_strategy():
    code = compile_to_code(
        {
            'type': 'object',
            'properties': {'host': {'type': 'string', 'format': 'hostname'}},
            'patternProperties': {'^x': {}},
        },
        regex_strategy=REGEX_STRATEGY_REGEX,
        regex_timeout=0.05,
    )
    # Schema pattern uses the controlled engine, library formats stay on re.
    assert "regex.compile('^x" in code
    assert "re.compile(" in code
    assert 'import regex' in code
    assert 'import re' in code


def test_compile_to_code_declares_stdlib_strategy():
    code = compile_to_code({'type': 'string', 'pattern': '^a'})
    tree = ast.parse(code)
    assert 'import re' in code
    assert 'import regex' not in code
    assert "REGEX_STRATEGY = RegexStrategy(name='stdlib', timeout=None)" in code
    assert 'def regex_pattern_search' in code
    # The generated matcher must go through the guarded helper.
    assert 'regex_pattern_search(REGEX_PATTERNS' in code


def test_compile_to_code_declares_regex_strategy_with_timeout():
    code = compile_to_code(
        {'type': 'string', 'pattern': '^a'},
        regex_strategy=REGEX_STRATEGY_REGEX,
        regex_timeout=0.05,
    )
    ast.parse(code)
    assert 'import regex' in code
    assert "REGEX_STRATEGY = RegexStrategy(name='regex', timeout=0.05)" in code
    assert 'compiled.search(value, timeout=0.05)' in code
    # No silent fallback to the stdlib engine.
    assert 'import re\n' not in code
    assert 're.compile' not in code


def test_compile_to_code_without_patterns_still_declares_strategy():
    code = compile_to_code({'type': 'string'}, regex_strategy=REGEX_STRATEGY_REGEX)
    assert "REGEX_STRATEGY = RegexStrategy(name='regex', timeout=0.1)" in code


def test_standalone_code_enforces_timeout(tmp_path, monkeypatch):
    code = compile_to_code(
        {'type': 'string', 'pattern': CATASTROPHIC_PATTERN},
        regex_strategy=REGEX_STRATEGY_REGEX,
        regex_timeout=0.05,
    )
    module_path = tmp_path / 'generated_regex.py'
    module_path.write_text(code)
    with monkeypatch.context() as patched:
        patched.syspath_prepend(tmp_path)
        generated = __import__('generated_regex')
    assert generated.REGEX_STRATEGY.name == REGEX_STRATEGY_REGEX
    assert generated.REGEX_STRATEGY.timeout == 0.05
    with pytest.raises(JsonSchemaRegexException):
        generated.validate(CATASTROPHIC_VALUE)
    assert generated.validate('aa') == 'aa'


def test_standalone_stdlib_code_does_not_import_regex_engine(tmp_path, monkeypatch):
    code = compile_to_code({'type': 'string', 'pattern': '^a'})
    module_path = tmp_path / 'generated_stdlib.py'
    module_path.write_text(code)
    with monkeypatch.context() as patched:
        patched.syspath_prepend(tmp_path)
        generated = __import__('generated_stdlib')
    assert generated.REGEX_STRATEGY.name == REGEX_STRATEGY_STDLIB
    assert generated.validate('abc') == 'abc'
    with pytest.raises(JsonSchemaValueException):
        generated.validate('1abc')


def test_standalone_code_reports_original_pattern_on_timeout(tmp_path, monkeypatch):
    code = compile_to_code(
        {'type': 'string', 'pattern': CATASTROPHIC_PATTERN},
        regex_strategy=REGEX_STRATEGY_REGEX,
        regex_timeout=0.05,
    )
    module_path = tmp_path / 'generated_pattern_name.py'
    module_path.write_text(code)
    with monkeypatch.context() as patched:
        patched.syspath_prepend(tmp_path)
        generated = __import__('generated_pattern_name')
    with pytest.raises(JsonSchemaRegexException) as exc:
        generated.validate(CATASTROPHIC_VALUE)
    assert exc.value.pattern == CATASTROPHIC_PATTERN
