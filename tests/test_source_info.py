import jmespath
from jmespath import exceptions
from jmespath import parser
from tests import unittest


class TestParserSourceSpans(unittest.TestCase):
    def setUp(self):
        self.parser = parser.Parser(track_positions=True)

    def parse(self, expression):
        return self.parser.parse(expression).parsed

    def test_nodes_have_spans_when_tracking(self):
        parsed = self.parse('foo.bar')
        self.assertEqual(parsed['span'], (0, 7))
        self.assertEqual(parsed['children'][0]['span'], (0, 3))
        self.assertEqual(parsed['children'][1]['span'], (4, 7))

    def test_no_spans_when_tracking_disabled(self):
        parsed = parser.Parser().parse('foo.bar').parsed
        self.assertNotIn('span', parsed)
        for child in parsed['children']:
            self.assertNotIn('span', child)

    def test_function_expression_span(self):
        parsed = self.parse('length(foo)')
        self.assertEqual(parsed['span'], (0, 11))
        self.assertEqual(parsed['children'][0]['span'], (7, 10))

    def test_backtick_literal_span(self):
        parsed = self.parse('length(`[1, 2]`)')
        self.assertEqual(parsed['children'][0]['span'], (7, 15))

    def test_backtick_literal_at_end_of_expression(self):
        # The token runs up to the EOF, the end position should
        # still be the exclusive end offset of the token.
        parsed = self.parse('foo | `{"a": 1}`')
        self.assertEqual(parsed['children'][1]['span'], (6, 16))

    def test_escaped_quoted_identifier_span(self):
        parsed = self.parse('"a\\"b".c')
        self.assertEqual(parsed['children'][0]['span'], (0, 6))
        self.assertEqual(parsed['span'], (0, 8))

    def test_escaped_backtick_literal_span(self):
        parsed = self.parse('`a\\`b`')
        self.assertEqual(parsed['span'], (0, 6))

    def test_projection_span(self):
        parsed = self.parse('a[*].b')
        self.assertEqual(parsed['type'], 'projection')
        self.assertEqual(parsed['span'], (0, 6))

    def test_cache_is_isolated_by_position_tracking(self):
        parser.Parser.purge()
        plain = jmespath.compile('foo')
        tracked = jmespath.compile('foo', track_positions=True)
        self.assertNotIn('span', plain.parsed)
        self.assertEqual(tracked.parsed['span'], (0, 3))


class TestRuntimeErrorSourceSpans(unittest.TestCase):
    def test_type_error_points_at_failing_argument(self):
        compiled = jmespath.compile('length(foo)', track_positions=True)
        with self.assertRaises(exceptions.JMESPathTypeError) as cm:
            compiled.search({'foo': 1})
        error = cm.exception
        self.assertEqual(error.expression, 'length(foo)')
        self.assertEqual(error.span, (7, 10))
        self.assertEqual(error.arg_index, 0)
        self.assertEqual(
            error.trace, [('function_expression', (0, 11))])

    def test_type_error_message_renders_span(self):
        compiled = jmespath.compile('length(foo)', track_positions=True)
        with self.assertRaises(exceptions.JMESPathTypeError) as cm:
            compiled.search({'foo': 1})
        rendered = str(cm.exception)
        self.assertIn('In function length(), invalid type for value',
                      rendered)
        self.assertIn('"length(foo)"', rendered)
        self.assertIn('^^^', rendered)

    def test_error_inside_nested_projection(self):
        compiled = jmespath.compile('a[*].length(@)', track_positions=True)
        with self.assertRaises(exceptions.JMESPathTypeError) as cm:
            compiled.search({'a': ['ok', 1]})
        error = cm.exception
        # The span points at the failing `@` argument.
        self.assertEqual(error.span, (12, 13))
        # The trace records the path out through the projection.
        self.assertEqual(
            error.trace,
            [('function_expression', (5, 14)),
             ('projection', (0, 14))])

    def test_error_inside_filter_projection(self):
        compiled = jmespath.compile('a[?length(b) > `2`].c',
                                    track_positions=True)
        with self.assertRaises(exceptions.JMESPathTypeError) as cm:
            compiled.search({'a': [{'b': 'x'}, {'b': 1}]})
        error = cm.exception
        self.assertEqual(error.expression, 'a[?length(b) > `2`].c')
        # The `b` argument of length() inside the filter.
        self.assertEqual(error.span, (10, 11))
        node_types = [entry[0] for entry in error.trace]
        self.assertIn('filter_projection', node_types)
        self.assertIn('function_expression', node_types)

    def test_subtype_error_points_at_argument(self):
        compiled = jmespath.compile('sum(foo)', track_positions=True)
        with self.assertRaises(exceptions.JMESPathTypeError) as cm:
            compiled.search({'foo': [1, 'a']})
        error = cm.exception
        self.assertEqual(error.span, (4, 7))
        self.assertEqual(error.arg_index, 0)

    def test_unknown_function_error_has_span(self):
        compiled = jmespath.compile('nope(@)', track_positions=True)
        with self.assertRaises(exceptions.UnknownFunctionError) as cm:
            compiled.search({})
        self.assertEqual(cm.exception.span, (0, 7))

    def test_original_exception_types_still_raised(self):
        compiled = jmespath.compile('length(foo)', track_positions=True)
        with self.assertRaises(ValueError):
            compiled.search({'foo': 1})
        with self.assertRaises(exceptions.JMESPathError):
            compiled.search({'foo': 1})

    def test_untracked_error_has_no_source_info(self):
        with self.assertRaises(exceptions.JMESPathTypeError) as cm:
            jmespath.search('length(foo)', {'foo': 1})
        error = cm.exception
        self.assertIsNone(error.span)
        self.assertIsNone(error.trace)
        self.assertNotIn('^', str(error))

    def test_successful_search_returns_plain_result(self):
        compiled = jmespath.compile('a[*].length(@)', track_positions=True)
        self.assertEqual(compiled.search({'a': ['x', 'yy']}), [1, 2])


if __name__ == '__main__':
    unittest.main()
