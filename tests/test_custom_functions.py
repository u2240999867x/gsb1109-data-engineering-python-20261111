import unittest

import jmespath
from jmespath import functions


class RecordingFunctions(functions.Functions):
    # Identity function: the return value depends on the argument, so
    # it cannot be constant-folded away, and every invocation is
    # recorded so tests can observe the exact evaluation count/order.
    def __init__(self):
        self.calls = []

    @functions.signature({'types': []})
    def _func_observe(self, value):
        self.calls.append(value)
        return value


class CustomFunctions(functions.Functions):
    @functions.signature({'types': ['string', 'array', 'object', 'null']})
    def _func_length0(self, s):
        return 0 if s is None else len(s)


class TestCustomFunctions(unittest.TestCase):
    def setUp(self):
        self.options = jmespath.Options(custom_functions=CustomFunctions())

    def test_null_to_nonetype(self):
        data = {
            'a': {
                'b': [1, 2, 3]
            }
        }

        self.assertEqual(jmespath.search('length0(a.b)', data, self.options), 3)
        self.assertEqual(jmespath.search('length0(a.c)', data, self.options), 0)


class TestShortCircuitEvaluation(unittest.TestCase):
    # Regression tests for short-circuit evaluation with custom
    # functions.  The JMESPath spec requires ``||`` and ``&&`` to
    # evaluate left-to-right and skip the right operand when the left
    # one already determines the result, and ``!`` to evaluate its
    # operand exactly once.  Falsy values are '', [], {}, null and
    # false; notably 0 is *truthy* in JMESPath.  Each test asserts the
    # exact sequence of recorded calls, so an implementation that
    # evaluates an operand one extra time fails even when the final
    # result happens to be the same.
    def setUp(self):
        self.recorder = RecordingFunctions()
        self.options = jmespath.Options(custom_functions=self.recorder)

    def assert_evaluation(self, expression, expected_result,
                          expected_calls, data=None):
        if data is None:
            data = {}
        result = jmespath.search(expression, data, self.options)
        self.assertEqual(result, expected_result)
        self.assertEqual(self.recorder.calls, expected_calls)

    def test_or_short_circuits_on_truthy_left(self):
        self.assert_evaluation(
            'observe(`"left"`) || observe(`"right"`)',
            'left', ['left'])

    def test_or_evaluates_right_on_null_left(self):
        self.assert_evaluation(
            'observe(`null`) || observe(`"right"`)',
            'right', [None, 'right'])

    def test_or_evaluates_right_on_false_left(self):
        self.assert_evaluation(
            'observe(`false`) || observe(`"right"`)',
            'right', [False, 'right'])

    def test_or_evaluates_right_on_empty_string_left(self):
        self.assert_evaluation(
            'observe(`""`) || observe(`"right"`)',
            'right', ['', 'right'])

    def test_or_evaluates_right_on_empty_array_left(self):
        self.assert_evaluation(
            'observe(`[]`) || observe(`"right"`)',
            'right', [[], 'right'])

    def test_or_evaluates_right_on_empty_object_left(self):
        self.assert_evaluation(
            'observe(`{}`) || observe(`"right"`)',
            'right', [{}, 'right'])

    def test_or_short_circuits_on_zero_left(self):
        # 0 is truthy in JMESPath, so the right side must not run.
        self.assert_evaluation(
            'observe(`0`) || observe(`"right"`)',
            0, [0])

    def test_or_chain_stops_after_first_truthy(self):
        self.assert_evaluation(
            'observe(`"a"`) || observe(`"b"`) || observe(`"c"`)',
            'a', ['a'])

    def test_or_chain_evaluates_left_to_right_until_truthy(self):
        self.assert_evaluation(
            'observe(`null`) || observe(`[]`) || observe(`"c"`)',
            'c', [None, [], 'c'])

    def test_and_evaluates_right_on_truthy_left(self):
        self.assert_evaluation(
            'observe(`"left"`) && observe(`"right"`)',
            'right', ['left', 'right'])

    def test_and_short_circuits_on_null_left(self):
        self.assert_evaluation(
            'observe(`null`) && observe(`"right"`)',
            None, [None])

    def test_and_short_circuits_on_false_left(self):
        self.assert_evaluation(
            'observe(`false`) && observe(`"right"`)',
            False, [False])

    def test_and_short_circuits_on_empty_string_left(self):
        self.assert_evaluation(
            'observe(`""`) && observe(`"right"`)',
            '', [''])

    def test_and_short_circuits_on_empty_array_left(self):
        self.assert_evaluation(
            'observe(`[]`) && observe(`"right"`)',
            [], [[]])

    def test_and_short_circuits_on_empty_object_left(self):
        self.assert_evaluation(
            'observe(`{}`) && observe(`"right"`)',
            {}, [{}])

    def test_and_evaluates_right_on_zero_left(self):
        # 0 is truthy in JMESPath, so the right side must run.
        self.assert_evaluation(
            'observe(`0`) && observe(`"right"`)',
            'right', [0, 'right'])

    def test_and_chain_evaluates_left_to_right(self):
        self.assert_evaluation(
            'observe(`"a"`) && observe(`"b"`) && observe(`"c"`)',
            'c', ['a', 'b', 'c'])

    def test_and_chain_stops_after_first_falsy(self):
        self.assert_evaluation(
            'observe(`"a"`) && observe(`null`) && observe(`"c"`)',
            None, ['a', None])

    def test_not_evaluates_operand_once_on_truthy(self):
        self.assert_evaluation('!observe(`"x"`)', False, ['x'])

    def test_not_evaluates_operand_once_on_null(self):
        self.assert_evaluation('!observe(`null`)', True, [None])

    def test_not_evaluates_operand_once_on_empty_array(self):
        self.assert_evaluation('!observe(`[]`)', True, [[]])

    def test_not_evaluates_operand_once_on_empty_object(self):
        self.assert_evaluation('!observe(`{}`)', True, [{}])

    def test_not_evaluates_operand_once_on_zero(self):
        # !0 is false: 0 is not special-cased as falsy in JMESPath.
        self.assert_evaluation('!observe(`0`)', False, [0])

    def test_short_circuit_applies_to_document_values(self):
        data = {'missing': None, 'fallback': 'default'}
        self.assert_evaluation(
            'observe(missing) || observe(fallback)',
            'default', [None, 'default'], data=data)
