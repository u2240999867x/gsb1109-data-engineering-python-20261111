import pytest

from fastjsonschema import (
    ErrorBudget,
    JsonSchemaValueException,
    JsonSchemaValuesException,
    compile,
    compile_to_code,
    validate,
)


ARRAY_SCHEMA = {'type': 'array', 'items': {'type': 'integer', 'minimum': 5}}
# [0] minimum, [1] type, [3] minimum -> three errors in this stable order.
ARRAY_DATA = [1, 'a', 7, 2]


def _messages(exc):
    return [error.message for error in exc.errors]


def test_error_budget_records_until_limit():
    budget = ErrorBudget(2)
    assert not budget.is_exhausted
    assert budget.record() is True
    assert budget.record() is True
    assert budget.is_exhausted
    assert budget.record() is False
    assert budget.overflowed


def test_error_budget_without_limit_never_stops():
    budget = ErrorBudget()
    for _ in range(100):
        assert budget.record() is True
    assert not budget.is_exhausted
    assert not budget.overflowed


def test_max_errors_keeps_stable_prefix_of_unlimited_run():
    unlimited = compile(ARRAY_SCHEMA, fast_fail=False)
    with pytest.raises(JsonSchemaValuesException) as exc_info:
        unlimited(ARRAY_DATA)
    expected = _messages(exc_info.value)
    assert len(expected) == 3

    for max_errors in (1, 2, 3):
        validator = compile(ARRAY_SCHEMA, fast_fail=False, max_errors=max_errors)
        with pytest.raises(JsonSchemaValuesException) as exc_info:
            validator(ARRAY_DATA)
        kept = _messages(exc_info.value)
        assert kept == expected[:max_errors]
        assert exc_info.value.total_errors == len(kept)


def test_truncation_and_unvalidated_locations():
    validator = compile(ARRAY_SCHEMA, fast_fail=False, max_errors=2)
    with pytest.raises(JsonSchemaValuesException) as exc_info:
        validator(ARRAY_DATA)
    exc = exc_info.value
    assert exc.truncated is True
    # The loop stopped before checking the item at index 2, so the remaining
    # items (including the bad index 3) were never walked.
    assert exc.unvalidated == ['data[2]']
    assert 'error budget exhausted' in exc.message


def test_exact_fit_budget_is_not_reported_as_truncated():
    validator = compile(ARRAY_SCHEMA, fast_fail=False, max_errors=3)
    with pytest.raises(JsonSchemaValuesException) as exc_info:
        validator(ARRAY_DATA)
    exc = exc_info.value
    assert len(exc.errors) == 3
    assert exc.truncated is False
    assert exc.unvalidated == []
    assert 'error budget exhausted' not in exc.message


def test_budget_larger_than_error_count_collects_everything():
    validator = compile(ARRAY_SCHEMA, fast_fail=False, max_errors=100)
    with pytest.raises(JsonSchemaValuesException) as exc_info:
        validator(ARRAY_DATA)
    exc = exc_info.value
    assert len(exc.errors) == 3
    assert exc.truncated is False


def test_loop_does_not_walk_items_after_budget_is_spent():
    validator = compile(ARRAY_SCHEMA, fast_fail=False, max_errors=1)
    big_data = [1, 'a', 'b', 'c', 'd']
    with pytest.raises(JsonSchemaValuesException) as exc_info:
        validator(big_data)
    assert len(exc_info.value.errors) == 1
    assert exc_info.value.unvalidated == ['data[1]']


def test_budget_is_fresh_on_each_validator_call():
    validator = compile(ARRAY_SCHEMA, fast_fail=False, max_errors=1)
    for _ in range(3):
        with pytest.raises(JsonSchemaValuesException) as exc_info:
            validator(ARRAY_DATA)
        assert len(exc_info.value.errors) == 1
    # A valid document still passes and does not leave any state behind.
    assert validator([5, 6]) == [5, 6]


def test_ref_validation_shares_the_budget():
    schema = {
        'type': 'object',
        'definitions': {'int': {'type': 'integer'}},
        'properties': {
            'arr': {'type': 'array', 'items': {'$ref': '#/definitions/int'}},
        },
    }
    validator = compile(schema, fast_fail=False, max_errors=3)
    with pytest.raises(JsonSchemaValuesException) as exc_info:
        validator({'arr': ['a', 'b', 'c', 'd', 'e']})
    exc = exc_info.value
    assert len(exc.errors) == 3
    assert _messages(exc) == [
        'data.arr[0] must be integer',
        'data.arr[1] must be integer',
        'data.arr[2] must be integer',
    ]
    assert exc.truncated is True
    assert exc.unvalidated == ['data.arr[3]']


def test_ref_property_validation_shares_the_budget():
    schema = {
        'type': 'object',
        'definitions': {'int': {'type': 'integer'}},
        'properties': {
            'a': {'$ref': '#/definitions/int'},
            'b': {'type': 'string'},
        },
    }
    validator = compile(schema, fast_fail=False, max_errors=1)
    with pytest.raises(JsonSchemaValuesException) as exc_info:
        validator({'a': 'x', 'b': 1})
    assert _messages(exc_info.value) == ['data.a must be integer']
    assert exc_info.value.truncated is True


def test_nested_validation_shares_the_budget():
    schema = {'type': 'array', 'maxItems': 1, 'items': {'$ref': '#'}}
    validator = compile(schema, fast_fail=False, max_errors=2)
    with pytest.raises(JsonSchemaValuesException) as exc_info:
        validator([[1, 2], [[]]])
    assert len(exc_info.value.errors) == 2
    assert exc_info.value.truncated is True


def test_object_loops_stop_when_budget_is_spent():
    schema = {
        'patternProperties': {'^i_': {'type': 'integer'}},
        'additionalProperties': {'type': 'string'},
    }
    validator = compile(schema, fast_fail=False, max_errors=2)
    with pytest.raises(JsonSchemaValuesException) as exc_info:
        validator({'i_a': 'x', 'i_b': 1, 'c': 2, 'i_d': 'y', 'e': 3})
    exc = exc_info.value
    assert len(exc.errors) == 2
    assert exc.truncated is True
    assert exc.unvalidated


def test_trial_subschemas_do_not_consume_the_budget():
    # anyOf tries its branches and catches their failures; those attempts are
    # control flow and must not spend the caller's budget.
    validator = compile(
        {'anyOf': [{'type': 'integer'}, {'type': 'string'}]},
        fast_fail=False, max_errors=5,
    )
    with pytest.raises(JsonSchemaValuesException) as exc_info:
        validator(1.5)
    assert _messages(exc_info.value) == ['data cannot be validated by any definition']
    assert exc_info.value.truncated is False


def test_contains_and_property_names_report_once_with_budget():
    contains = compile({'contains': {'type': 'number'}}, fast_fail=False, max_errors=1)
    with pytest.raises(JsonSchemaValuesException) as exc_info:
        contains(['a', 'b', 'c'])
    assert _messages(exc_info.value) == ['data must contain one of contains definition']

    names = compile({'propertyNames': {'maxLength': 3}}, fast_fail=False, max_errors=1)
    with pytest.raises(JsonSchemaValuesException) as exc_info:
        names({'foobar': 1})
    assert _messages(exc_info.value) == ['data must be named by propertyName definition']


def test_budget_works_without_detailed_exceptions():
    validator = compile(
        ARRAY_SCHEMA, fast_fail=False, max_errors=2, detailed_exceptions=False,
    )
    with pytest.raises(JsonSchemaValuesException) as exc_info:
        validator(ARRAY_DATA)
    assert len(exc_info.value.errors) == 2
    assert exc_info.value.truncated is True


def test_adjacent_error_rules_each_consume_the_budget():
    # Two rules failing at the same location produce adjacent record() checks.
    # They must not be merged into a single check by the block optimizer,
    # otherwise the second error would not count against the budget.
    schema = {'type': 'string', 'minLength': 3, 'pattern': '^z'}
    code = compile_to_code(schema, fast_fail=False, max_errors=1)
    assert code.count('if error_budget.record():') >= 2

    one = compile(schema, fast_fail=False, max_errors=1)
    with pytest.raises(JsonSchemaValuesException) as exc_info:
        one('a')
    assert len(exc_info.value.errors) == 1

    two = compile(schema, fast_fail=False, max_errors=2)
    with pytest.raises(JsonSchemaValuesException) as exc_info:
        two('a')
    assert len(exc_info.value.errors) == 2
    assert exc_info.value.truncated is False


def test_validate_one_shot_api_supports_budget():
    with pytest.raises(JsonSchemaValuesException) as exc_info:
        validate(ARRAY_SCHEMA, ARRAY_DATA, fast_fail=False, max_errors=1)
    assert len(exc_info.value.errors) == 1


def test_default_first_error_mode_is_unchanged():
    validator = compile(ARRAY_SCHEMA)
    with pytest.raises(JsonSchemaValueException) as exc_info:
        validator(ARRAY_DATA)
    assert exc_info.value.message == 'data[0] must be bigger than or equal to 5'


def test_no_budget_means_generated_code_has_no_budget_scaffolding():
    code = compile_to_code(ARRAY_SCHEMA, fast_fail=False)
    assert 'error_budget' not in code
    assert 'ErrorBudget' not in code


def test_generated_code_file_supports_budget(tmp_path, monkeypatch):
    code = compile_to_code(ARRAY_SCHEMA, fast_fail=False, max_errors=2)
    assert 'ErrorBudget' in code
    module_path = tmp_path / 'schema_budget.py'
    module_path.write_text(code)
    with monkeypatch.context() as m:
        m.syspath_prepend(tmp_path)
        from schema_budget import validate as file_validate
    with pytest.raises(JsonSchemaValuesException) as exc_info:
        file_validate(ARRAY_DATA)
    assert len(exc_info.value.errors) == 2
    assert exc_info.value.truncated is True


@pytest.mark.parametrize('kwargs', [
    {'fast_fail': True, 'max_errors': 5},
    {'fast_fail': False, 'max_errors': 0},
    {'fast_fail': False, 'max_errors': -1},
    {'fast_fail': False, 'max_errors': '5'},
    {'fast_fail': False, 'max_errors': True},
])
def test_invalid_max_errors(kwargs):
    with pytest.raises(ValueError):
        compile(ARRAY_SCHEMA, **kwargs)
    with pytest.raises(ValueError):
        compile_to_code(ARRAY_SCHEMA, **kwargs)
