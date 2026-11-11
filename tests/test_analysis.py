#!/usr/bin/env python
from tests import unittest

import jmespath
from jmespath import analysis
from jmespath import exceptions
from jmespath import functions


class TestFieldRequirementAnalysis(unittest.TestCase):
    def assert_analysis(self, expression, paths, unknowns, complete):
        result = jmespath.analyze(expression)
        self.assertEqual(result.paths, tuple(paths))
        self.assertEqual(result.unknowns, tuple(unknowns))
        self.assertEqual(result.is_complete, complete)
        return result

    # ---- concrete paths -------------------------------------------------

    def test_single_field(self):
        self.assert_analysis('a', ['a'], [], True)

    def test_nested_fields(self):
        self.assert_analysis('a.b.c', ['a', 'a.b', 'a.b.c'], [], True)

    def test_quoted_field_name(self):
        self.assert_analysis('"weird.name".x',
                             ['weird.name', 'weird.name.x'], [], True)

    def test_fixed_index_is_concrete(self):
        self.assert_analysis('a[0].b',
                             ['a', 'a[0]', 'a[0].b'], [], True)

    def test_multi_select_hash(self):
        self.assert_analysis('{x: a, y: b.c}',
                             ['a', 'b', 'b.c'], [], True)

    def test_multi_select_list(self):
        self.assert_analysis('[a, b.c]',
                             ['a', 'b', 'b.c'], [], True)

    def test_boolean_branches(self):
        # Both branches of ``||`` and ``&&`` must be requested.
        self.assert_analysis('a.b || c.d',
                             ['a', 'a.b', 'c', 'c.d'], [], True)
        self.assert_analysis('a && b', ['a', 'b'], [], True)
        self.assert_analysis('!a', ['a'], [], True)

    def test_pipe_threads_concrete_path(self):
        self.assert_analysis(
            'a.b | c.d',
            ['a', 'a.b', 'a.b.c', 'a.b.c.d'], [], True)

    def test_pipe_with_slice_arg_is_unknown(self):
        self.assert_analysis('a.b | length(@)', ['a'], ['a.b'], False)

    # ---- unknown regions ------------------------------------------------

    def test_list_wildcard(self):
        self.assert_analysis('foo[*].bar', [], ['foo'], False)

    def test_object_wildcard(self):
        self.assert_analysis('foo.*.bar', [], ['foo'], False)

    def test_root_wildcard(self):
        self.assert_analysis('*', [], ['*'], False)

    def test_current_node(self):
        self.assert_analysis('@', [], ['*'], False)

    def test_current_node_as_pipe_input_is_transparent(self):
        self.assert_analysis('@.a', ['a'], [], True)
        self.assert_analysis('@ | a', ['a'], [], True)

    def test_current_node_value_is_still_required(self):
        self.assert_analysis('!@', [], ['*'], False)
        self.assert_analysis('@ || b', [], ['*'], False)
        self.assert_analysis('{x: @}', [], ['*'], False)

    def test_filter_projection(self):
        self.assert_analysis('users[?age > `18`].name',
                             [], ['users'], False)

    def test_flatten_projection(self):
        self.assert_analysis('foo[].bar', [], ['foo'], False)

    def test_slice_projection(self):
        self.assert_analysis('a[1:3].b', [], ['a'], False)

    def test_nested_projection_names_outer_container(self):
        self.assert_analysis('a.b[*].c.d', ['a'], ['a.b'], False)

    def test_pipe_keeps_unknown(self):
        self.assert_analysis('foo[*] | [0].bar', [], ['foo'], False)

    def test_or_does_not_hide_unknown(self):
        # The dynamic branch must not make us claim the data is complete.
        self.assert_analysis('foo[*].bar || baz', ['baz'], ['foo'], False)

    # ---- functions ------------------------------------------------------

    def test_scalar_function_requests_argument(self):
        self.assert_analysis('length(foo)', [], ['foo'], False)

    def test_literal_only_function_requires_nothing(self):
        self.assert_analysis('abs(`-5`)', [], [], True)

    def test_function_with_two_arguments(self):
        self.assert_analysis('merge(a, b)', [], ['a', 'b'], False)

    def test_function_with_expression_reference(self):
        self.assert_analysis('sort_by(users, &age)', [], ['users'], False)

    def test_map_requests_array_argument(self):
        self.assert_analysis('map(&x, items)', [], ['items'], False)

    def test_chained_function_result_adds_no_root_path(self):
        self.assert_analysis('max_by(users, &age).name',
                             [], ['users'], False)

    def test_unknown_function_is_treated_conservatively(self):
        # An unknown function never gets called; its inputs are requested.
        self.assert_analysis('made_up(a)', [], ['a'], False)

    def test_custom_functions_are_never_invoked(self):
        original = functions.Functions.call_function

        def fail(*args, **kwargs):
            raise AssertionError('custom function was invoked')

        functions.Functions.call_function = fail
        try:
            result = jmespath.analyze('sort_by(users, &age)')
        finally:
            functions.Functions.call_function = original
        self.assertEqual(result.unknowns, ('users',))

    # ---- result object --------------------------------------------------

    def test_paths_and_unknowns_are_sorted_and_unique(self):
        result = jmespath.analyze('foo[*].bar')
        self.assertEqual(result.unknowns, ('foo',))

    def test_as_dict(self):
        result = jmespath.analyze('a.b')
        self.assertEqual(result.as_dict(), {
            'expression': 'a.b',
            'paths': ['a', 'a.b'],
            'unknowns': [],
            'complete': True,
        })

    def test_equality(self):
        first = jmespath.analyze('a.b')
        second = jmespath.analyze('a.b')
        self.assertEqual(first, second)
        self.assertNotEqual(first, jmespath.analyze('a.c'))

    def test_result_is_immutable_tuple(self):
        result = jmespath.analyze('a.b')
        self.assertIsInstance(result.paths, tuple)
        self.assertIsInstance(result.unknowns, tuple)

    def test_invalid_expression_raises_parse_error(self):
        with self.assertRaises(exceptions.ParseError):
            jmespath.analyze('foo[')

    def test_unhandled_node_type_is_not_silently_ignored(self):
        visitor = analysis._FieldRequirementVisitor()
        with self.assertRaises(NotImplementedError):
            visitor.analyze({'type': 'not_a_real_node', 'children': []})


if __name__ == '__main__':
    unittest.main()
