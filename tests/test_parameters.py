import threading
from types import MappingProxyType

from tests import unittest

import jmespath
from jmespath import ast
from jmespath import exceptions
from jmespath import lexer
from jmespath import parser
from jmespath import visitor


class TestParameterLexer(unittest.TestCase):
    def setUp(self):
        self.lexer = lexer.Lexer()

    def test_parameter_token(self):
        tokens = list(self.lexer.tokenize('$threshold'))
        self.assertEqual(
            tokens,
            [{'type': 'parameter', 'value': 'threshold',
              'start': 0, 'end': len('$threshold')},
             {'type': 'eof', 'value': '',
              'start': len('$threshold'), 'end': len('$threshold')}])

    def test_parameter_with_underscores_and_digits(self):
        tokens = list(self.lexer.tokenize('$region_name2'))
        self.assertEqual(tokens[0]['type'], 'parameter')
        self.assertEqual(tokens[0]['value'], 'region_name2')

    def test_dollar_without_name_is_lexer_error(self):
        with self.assertRaises(exceptions.LexerError):
            list(self.lexer.tokenize('$'))

    def test_dollar_followed_by_digit_is_lexer_error(self):
        with self.assertRaises(exceptions.LexerError):
            list(self.lexer.tokenize('$1abc'))


class TestParameterParser(unittest.TestCase):
    def setUp(self):
        self.parser = parser.Parser()

    def test_parameter_parses_to_parameter_node(self):
        parsed = self.parser.parse('$threshold')
        self.assertEqual(parsed.parsed, ast.parameter('threshold'))

    def test_parameter_inside_filter_projection(self):
        parsed = self.parser.parse('items[?score > $threshold]')
        condition = parsed.parsed['children'][2]
        self.assertEqual(condition['children'][1],
                         ast.parameter('threshold'))


class TestParameterSearch(unittest.TestCase):
    def setUp(self):
        self.data = {
            'items': [
                {'name': 'a', 'region': 'cn', 'score': 10},
                {'name': 'b', 'region': 'us', 'score': 25},
                {'name': 'c', 'region': 'cn', 'score': 40},
            ]
        }

    def test_parameter_in_filter_projection(self):
        compiled = jmespath.compile(
            'items[?score > $threshold && region == $region].name')
        self.assertEqual(
            compiled.search(self.data,
                            params={'threshold': 20, 'region': 'cn'}),
            ['c'])

    def test_same_compiled_expression_reuses_different_params(self):
        compiled = jmespath.compile(
            'items[?score > $threshold && region == $region].name')
        self.assertEqual(
            compiled.search(self.data,
                            params={'threshold': 5, 'region': 'cn'}),
            ['a', 'c'])
        self.assertEqual(
            compiled.search(self.data,
                            params={'threshold': 30, 'region': 'us'}),
            [])

    def test_parameter_stable_inside_projection(self):
        # The parameter must keep referencing the call-scoped value
        # instead of switching to the node visited in the projection.
        compiled = jmespath.compile(
            'items[?score > $threshold].{name: name, cutoff: $threshold}')
        result = compiled.search(self.data, params={'threshold': 20})
        self.assertEqual(
            result,
            [{'name': 'b', 'cutoff': 20},
             {'name': 'c', 'cutoff': 20}])

    def test_parameter_in_value_projection(self):
        compiled = jmespath.compile('regions.*[?score > $threshold].name')
        data = {'regions': {
            'cn': [{'name': 'a', 'score': 10},
                   {'name': 'c', 'score': 40}],
            'us': [{'name': 'b', 'score': 25}],
        }}
        self.assertEqual(
            compiled.search(data, params={'threshold': 15}),
            [['c'], ['b']])

    def test_parameter_as_function_argument(self):
        compiled = jmespath.compile('contains($tags, name)')
        self.assertTrue(
            compiled.search({'name': 'x'},
                            params={'tags': ['x', 'y']}))

    def test_parameter_supports_all_json_value_types(self):
        self.assertEqual(
            jmespath.search('[?a == $flag]',
                            [{'a': True}, {'a': False}],
                            params={'flag': True}),
            [{'a': True}])
        self.assertIsNone(
            jmespath.search('$maybe', {}, params={'maybe': None}))
        self.assertEqual(
            jmespath.search('$nested.key', {},
                            params={'nested': {'key': 'value'}}),
            'value')
        self.assertEqual(
            jmespath.search('$nums[1]', {}, params={'nums': [1, 2, 3]}),
            2)

    def test_unknown_parameter_raises_defined_error(self):
        compiled = jmespath.compile('items[?score > $threshold]')
        with self.assertRaises(exceptions.UndefinedParameterError) as cm:
            compiled.search(self.data, params={'region': 'cn'})
        self.assertEqual(cm.exception.parameter_name, 'threshold')
        self.assertIn('$threshold', str(cm.exception))

    def test_parameter_without_params_raises(self):
        compiled = jmespath.compile('$value')
        with self.assertRaises(exceptions.UndefinedParameterError):
            compiled.search({})

    def test_extra_unused_params_are_ignored(self):
        compiled = jmespath.compile('items[0].name')
        self.assertEqual(
            compiled.search(self.data, params={'unused': 'ignored'}),
            'a')

    def test_module_level_search_accepts_params(self):
        self.assertEqual(
            jmespath.search('a > $n', {'a': 3}, params={'n': 2}),
            True)

    def test_quoted_value_cannot_change_query_structure(self):
        compiled = jmespath.compile(
            'items[?region == $region].name')
        injection = "cn' || '1' == '1"
        # The malicious string is treated as plain data, so no item
        # matches and the expression structure is untouched.
        self.assertEqual(
            compiled.search(self.data, params={'region': injection}),
            [])
        self.assertEqual(
            compiled.search(self.data, params={'region': 'cn'}),
            ['a', 'c'])

    def test_params_binding_is_read_only(self):
        interpreter = visitor.TreeInterpreter(params={'a': 1})
        self.assertIsInstance(interpreter._params, MappingProxyType)
        with self.assertRaises(TypeError):
            interpreter._params['a'] = 2

    def test_params_are_not_stored_on_compiled_result(self):
        compiled = jmespath.compile('$a')
        compiled.search({}, params={'a': 1})
        self.assertFalse(hasattr(compiled, 'params'))
        self.assertNotIn('params', compiled.__dict__)

    def test_concurrent_searches_with_shared_compiled_expression(self):
        compiled = jmespath.compile(
            'items[?score > $threshold].name')
        failures = []

        def worker(threshold):
            expected = [item['name'] for item in self.data['items']
                        if item['score'] > threshold]
            for _ in range(500):
                if compiled.search(
                        self.data,
                        params={'threshold': threshold}) != expected:
                    failures.append(threshold)

        threads = [threading.Thread(target=worker, args=(t,))
                   for t in (5, 15, 30)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(failures, [])
