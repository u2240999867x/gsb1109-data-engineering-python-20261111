import unittest

import jmespath
from jmespath import exceptions
from jmespath import functions


class OverloadedFunctions(functions.Functions):

    # The same function name with one implementation per argument type.
    @functions.signature({'types': ['string']})
    @functions.signature({'types': ['number']})
    def _func_normalize(self, value):
        if isinstance(value, str):
            return 'string'
        return 'number'

    # Array overloads must be selected from the array element type.
    @functions.signature({'types': ['array-string']})
    @functions.signature({'types': ['array-number']})
    def _func_bucket(self, values):
        if values and isinstance(values[0], str):
            return 'strings'
        return 'numbers'

    # A fixed arity overload alongside a variadic one.
    @functions.signature({'types': ['number']}, {'types': ['number']})
    @functions.signature({'types': ['string'], 'variadic': True})
    def _func_pick(self, *arguments):
        if len(arguments) == 2:
            return 'two-numbers'
        return 'variadic'


class ReversedOverloadedFunctions(functions.Functions):

    # Same overloads declared in the opposite decoration order.  Resolution
    # must not depend on which signature was registered first.
    @functions.signature({'types': ['number']})
    @functions.signature({'types': ['string']})
    def _func_normalize(self, value):
        if isinstance(value, str):
            return 'string'
        return 'number'

    @functions.signature({'types': ['array-number']})
    @functions.signature({'types': ['array-string']})
    def _func_bucket(self, values):
        if values and isinstance(values[0], str):
            return 'strings'
        return 'numbers'

    @functions.signature({'types': ['string'], 'variadic': True})
    @functions.signature({'types': ['number']}, {'types': ['number']})
    def _func_pick(self, *arguments):
        if len(arguments) == 2:
            return 'two-numbers'
        return 'variadic'


class TestFunctionOverloads(unittest.TestCase):

    CLASSES = (OverloadedFunctions, ReversedOverloadedFunctions)

    def _search(self, expression, data, cls):
        options = jmespath.Options(custom_functions=cls())
        return jmespath.search(expression, data, options)

    def _assert_for_all_classes(self, expression, data, expected):
        for cls in self.CLASSES:
            self.assertEqual(
                self._search(expression, data, cls), expected,
                'expression %r failed for %s' % (expression, cls.__name__))

    def test_selects_overload_by_scalar_type(self):
        self._assert_for_all_classes("normalize('hello')", {}, 'string')
        self._assert_for_all_classes("normalize(`1`)", {}, 'number')

    def test_selects_overload_by_array_element_type(self):
        data = {'numbers': [1, 2], 'strings': ['a', 'b']}
        self._assert_for_all_classes('bucket(numbers)', data, 'numbers')
        self._assert_for_all_classes('bucket(strings)', data, 'strings')

    def test_empty_array_does_not_resolve_by_registration_order(self):
        data = {'empty': []}
        for cls in self.CLASSES:
            with self.assertRaises(
                    exceptions.AmbiguousFunctionResolutionError):
                self._search('bucket(empty)', data, cls)

    def test_wrong_array_element_type_has_no_match(self):
        for cls in self.CLASSES:
            with self.assertRaises(
                    exceptions.InvalidFunctionArgumentError):
                self._search('bucket([`true`])', {}, cls)

    def test_variadic_and_fixed_arity_are_distinguished(self):
        self._assert_for_all_classes("pick(`1`, `2`)", {}, 'two-numbers')
        self._assert_for_all_classes(
            "pick('a', 'b', 'c')", {}, 'variadic')

    def test_no_matching_signature_raises(self):
        for cls in self.CLASSES:
            with self.assertRaises(
                    exceptions.InvalidFunctionArgumentError):
                self._search("normalize(`true`)", {}, cls)
            with self.assertRaises(
                    exceptions.InvalidFunctionArgumentError):
                self._search('pick()', {}, cls)
            with self.assertRaises(
                    exceptions.InvalidFunctionArgumentError):
                self._search("pick(`true`)", {}, cls)

    def test_ambiguous_signatures_raise(self):
        class Ambiguous(functions.Functions):
            @functions.signature({'types': []})
            @functions.signature({'types': ['string']})
            def _func_dup(self, value):
                return value

        options = jmespath.Options(custom_functions=Ambiguous())
        with self.assertRaises(
                exceptions.AmbiguousFunctionResolutionError):
            jmespath.search("dup('x')", {}, options)

    def test_single_signature_remains_supported(self):
        class Single(functions.Functions):
            @functions.signature({'types': ['number']})
            def _func_double(self, value):
                return value * 2

        options = jmespath.Options(custom_functions=Single())
        self.assertEqual(jmespath.search('double(`21`)', {}, options), 42)
        spec = Single.FUNCTION_TABLE['double']
        self.assertEqual(len(spec['signatures']), 1)
        with self.assertRaises(exceptions.JMESPathTypeError):
            jmespath.search("double('x')", {}, options)
        with self.assertRaises(exceptions.ArityError):
            jmespath.search('double(`1`, `2`)', {}, options)

    def test_decorated_method_keeps_signature_attributes(self):
        method = OverloadedFunctions._func_normalize
        self.assertEqual(len(method.signatures), 2)
        # The legacy attribute points at the most recently declared overload.
        self.assertIs(method.signature, method.signatures[0])
