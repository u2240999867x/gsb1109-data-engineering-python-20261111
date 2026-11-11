"""Top down operator precedence parser.

This is an implementation of Vaughan R. Pratt's
"Top Down Operator Precedence" parser.
(http://dl.acm.org/citation.cfm?doid=512927.512931).

These are some additional resources that help explain the
general idea behind a Pratt parser:

* http://effbot.org/zone/simple-top-down-parsing.htm
* http://javascript.crockford.com/tdop/tdop.html

A few notes on the implementation.

* All the nud/led tokens are on the Parser class itself, and are dispatched
  using getattr().  This keeps all the parsing logic contained to a single
  class.
* We use two passes through the data.  One to create a list of token,
  then one pass through the tokens to create the AST.  While the lexer actually
  yields tokens, we convert it to a list so we can easily implement two tokens
  of lookahead.  A previous implementation used a fixed circular buffer, but it
  was significantly slower.  Also, the average jmespath expression typically
  does not have a large amount of token so this is not an issue.  And
  interestingly enough, creating a token list first is actually faster than
  consuming from the token iterator one token at a time.

"""
from jmespath import lexer
from jmespath.compat import with_repr_method
from jmespath import ast
from jmespath import exceptions
from jmespath import visitor


class Parser(object):
    BINDING_POWER = {
        'eof': 0,
        'unquoted_identifier': 0,
        'quoted_identifier': 0,
        'literal': 0,
        'rbracket': 0,
        'rparen': 0,
        'comma': 0,
        'rbrace': 0,
        'number': 0,
        'current': 0,
        'expref': 0,
        'colon': 0,
        'pipe': 1,
        'or': 2,
        'and': 3,
        'eq': 5,
        'gt': 5,
        'lt': 5,
        'gte': 5,
        'lte': 5,
        'ne': 5,
        'flatten': 9,
        # Everything above stops a projection.
        'star': 20,
        'filter': 21,
        'dot': 40,
        'not': 45,
        'lbrace': 50,
        'lbracket': 55,
        'lparen': 60,
    }
    # The maximum binding power for a token that can stop
    # a projection.
    _PROJECTION_STOP = 10
    # The _MAX_SIZE most recent expressions are cached in
    # _CACHE dict.
    _CACHE = {}
    _MAX_SIZE = 512

    def __init__(self, lookahead=2, track_positions=False):
        self.tokenizer = None
        self._tokens = [None] * lookahead
        self._buffer_size = lookahead
        self._index = 0
        #: When enabled, every AST node is annotated with a 'span'
        #:  key holding the (start, end) character offsets of the
        #:  node within the original expression.  The interpreter
        #:  uses these spans to point runtime errors at the exact
        #:  segment of the expression that failed.
        self._track_positions = track_positions

    def parse(self, expression):
        cache_key = (expression, self._track_positions)
        try:
            return self._CACHE[cache_key]
        except KeyError:
            pass
        parsed_result = self._do_parse(expression)
        if len(self._CACHE) >= self._MAX_SIZE:
            try:
                del self._CACHE[next(iter(self._CACHE))]
            except (KeyError, StopIteration, RuntimeError):
                # KeyError - Another thread else already deleted the key.
                # RuntimeError - Another modified the cache.
                # StopIteration - (Unlikely) Cache is empty.
                #
                # If we encounter an error we should NOT be adding to the
                # cache.  To ensure we do not exceed self._MAX_SIZE, we
                # can only add to the cache if we successfully removed
                # an element from the cache, otherwise this can grow
                # unbounded.
                return parsed_result
        self._CACHE[cache_key] = parsed_result
        return parsed_result

    def _do_parse(self, expression):
        try:
            return self._parse(expression)
        except exceptions.LexerError as e:
            e.expression = expression
            raise
        except exceptions.IncompleteExpressionError as e:
            e.set_expression(expression)
            raise
        except exceptions.ParseError as e:
            e.expression = expression
            raise

    def _parse(self, expression):
        self.tokenizer = lexer.Lexer().tokenize(expression)
        self._tokens = list(self.tokenizer)
        self._index = 0
        parsed = self._expression(binding_power=0)
        if not self._current_token() == 'eof':
            t = self._lookahead_token(0)
            raise exceptions.ParseError(t['start'], t['value'], t['type'],
                                        "Unexpected token: %s" % t['value'])
        return ParsedResult(expression, parsed)

    def _expression(self, binding_power=0):
        left_token = self._lookahead_token(0)
        self._advance()
        nud_function = getattr(
            self, '_token_nud_%s' % left_token['type'],
            self._error_nud_token)
        left = nud_function(left_token)
        current_token = self._current_token()
        while binding_power < self.BINDING_POWER[current_token]:
            led = getattr(self, '_token_led_%s' % current_token, None)
            if led is None:
                error_token = self._lookahead_token(0)
                self._error_led_token(error_token)
            else:
                self._advance()
                left = led(left)
                current_token = self._current_token()
        return left

    def _node_span(self, node, start, end=None):
        # Attach a (start, end) source span to a node when position
        # tracking is enabled.  By default ``end`` is the end of the
        # most recently consumed token, which works because nodes are
        # created right after their final token has been consumed.
        if self._track_positions:
            if end is None:
                end = self._tokens[self._index - 1]['end']
            node['span'] = (start, end)
        return node

    def _span_of(self, node):
        # The start position of a previously parsed node.  The
        # fallback is never used because _node_span() is a no-op
        # whenever position tracking is disabled.
        if self._track_positions:
            return node['span'][0]
        return 0

    def _identity_at(self, position):
        # An identity node standing in for an implicit current node,
        # given an empty span at the provided position.
        return self._node_span(ast.identity(), position, position)

    def _token_nud_literal(self, token):
        return self._node_span(ast.literal(token['value']),
                               token['start'], token['end'])

    def _token_nud_unquoted_identifier(self, token):
        return self._node_span(ast.field(token['value']),
                               token['start'], token['end'])

    def _token_nud_quoted_identifier(self, token):
        field = ast.field(token['value'])
        # You can't have a quoted identifier as a function
        # name.
        if self._current_token() == 'lparen':
            t = self._lookahead_token(0)
            raise exceptions.ParseError(
                0, t['value'], t['type'],
                'Quoted identifier not allowed for function names.')
        return self._node_span(field, token['start'], token['end'])

    def _token_nud_star(self, token):
        left = self._identity_at(token['start'])
        if self._current_token() == 'rbracket':
            right = self._identity_at(token['end'])
        else:
            right = self._parse_projection_rhs(self.BINDING_POWER['star'])
        return self._node_span(ast.value_projection(left, right),
                               token['start'])

    def _token_nud_filter(self, token):
        return self._token_led_filter(self._identity_at(token['start']))

    def _token_nud_lbrace(self, token):
        return self._parse_multi_select_hash()

    def _token_nud_lparen(self, token):
        expression = self._expression()
        self._match('rparen')
        return expression

    def _token_nud_flatten(self, token):
        left = self._node_span(
            ast.flatten(self._identity_at(token['start'])),
            token['start'], token['end'])
        right = self._parse_projection_rhs(
            self.BINDING_POWER['flatten'])
        return self._node_span(ast.projection(left, right), token['start'])

    def _token_nud_not(self, token):
        expr = self._expression(self.BINDING_POWER['not'])
        return self._node_span(ast.not_expression(expr), token['start'])

    def _token_nud_lbracket(self, token):
        if self._current_token() in ['number', 'colon']:
            right = self._parse_index_expression()
            # We could optimize this and remove the identity() node.
            # We don't really need an index_expression node, we can
            # just use emit an index node here if we're not dealing
            # with a slice.
            return self._project_if_slice(
                self._identity_at(token['start']), right)
        elif self._current_token() == 'star' and \
                self._lookahead(1) == 'rbracket':
            self._advance()
            self._advance()
            right = self._parse_projection_rhs(self.BINDING_POWER['star'])
            return self._node_span(
                ast.projection(self._identity_at(token['start']), right),
                token['start'])
        else:
            return self._parse_multi_select_list()

    def _parse_index_expression(self):
        # We're here:
        # [<current>
        #  ^
        #  | current token
        start = self._tokens[self._index - 1]['start']
        if (self._lookahead(0) == 'colon' or
                self._lookahead(1) == 'colon'):
            return self._parse_slice_expression()
        else:
            # Parse the syntax [number]
            node = ast.index(self._lookahead_token(0)['value'])
            self._advance()
            self._match('rbracket')
            return self._node_span(node, start)

    def _parse_slice_expression(self):
        # [start:end:step]
        # Where start, end, and step are optional.
        # The last colon is optional as well.
        start = self._tokens[self._index - 1]['start']
        parts = [None, None, None]
        index = 0
        current_token = self._current_token()
        while not current_token == 'rbracket' and index < 3:
            if current_token == 'colon':
                index += 1
                if index == 3:
                    self._raise_parse_error_for_token(
                        self._lookahead_token(0), 'syntax error')
                self._advance()
            elif current_token == 'number':
                parts[index] = self._lookahead_token(0)['value']
                self._advance()
            else:
                self._raise_parse_error_for_token(
                    self._lookahead_token(0), 'syntax error')
            current_token = self._current_token()
        self._match('rbracket')
        return self._node_span(ast.slice(*parts), start)

    def _token_nud_current(self, token):
        return self._node_span(ast.current_node(),
                               token['start'], token['end'])

    def _token_nud_expref(self, token):
        expression = self._expression(self.BINDING_POWER['expref'])
        return self._node_span(ast.expref(expression), token['start'])

    def _token_led_dot(self, left):
        if not self._current_token() == 'star':
            right = self._parse_dot_rhs(self.BINDING_POWER['dot'])
            if left['type'] == 'subexpression':
                left['children'].append(right)
                return self._node_span(left, self._span_of(left))
            else:
                return self._node_span(ast.subexpression([left, right]),
                                       self._span_of(left))
        else:
            # We're creating a projection.
            self._advance()
            right = self._parse_projection_rhs(
                self.BINDING_POWER['dot'])
            return self._node_span(ast.value_projection(left, right),
                                   self._span_of(left))

    def _token_led_pipe(self, left):
        right = self._expression(self.BINDING_POWER['pipe'])
        return self._node_span(ast.pipe(left, right), self._span_of(left))

    def _token_led_or(self, left):
        right = self._expression(self.BINDING_POWER['or'])
        return self._node_span(ast.or_expression(left, right),
                               self._span_of(left))

    def _token_led_and(self, left):
        right = self._expression(self.BINDING_POWER['and'])
        return self._node_span(ast.and_expression(left, right),
                               self._span_of(left))

    def _token_led_lparen(self, left):
        if left['type'] != 'field':
            #  0 - first func arg or closing paren.
            # -1 - '(' token
            # -2 - invalid function "name".
            prev_t = self._lookahead_token(-2)
            raise exceptions.ParseError(
                prev_t['start'], prev_t['value'], prev_t['type'],
                "Invalid function name '%s'" % prev_t['value'])
        name = left['value']
        args = []
        while not self._current_token() == 'rparen':
            expression = self._expression()
            if self._current_token() == 'comma':
                self._match('comma')
            args.append(expression)
        self._match('rparen')
        function_node = ast.function_expression(name, args)
        return self._node_span(function_node, self._span_of(left))

    def _token_led_filter(self, left):
        # Filters are projections.
        condition = self._expression(0)
        self._match('rbracket')
        if self._current_token() == 'flatten':
            right = self._identity_at(self._tokens[self._index - 1]['end'])
        else:
            right = self._parse_projection_rhs(self.BINDING_POWER['filter'])
        node = ast.filter_projection(left, right, condition)
        return self._node_span(node, self._span_of(left))

    def _token_led_eq(self, left):
        return self._parse_comparator(left, 'eq')

    def _token_led_ne(self, left):
        return self._parse_comparator(left, 'ne')

    def _token_led_gt(self, left):
        return self._parse_comparator(left, 'gt')

    def _token_led_gte(self, left):
        return self._parse_comparator(left, 'gte')

    def _token_led_lt(self, left):
        return self._parse_comparator(left, 'lt')

    def _token_led_lte(self, left):
        return self._parse_comparator(left, 'lte')

    def _token_led_flatten(self, left):
        left = self._node_span(ast.flatten(left), self._span_of(left))
        right = self._parse_projection_rhs(
            self.BINDING_POWER['flatten'])
        return self._node_span(ast.projection(left, right),
                               self._span_of(left))

    def _token_led_lbracket(self, left):
        token = self._lookahead_token(0)
        if token['type'] in ['number', 'colon']:
            right = self._parse_index_expression()
            if left['type'] == 'index_expression':
                # Optimization: if the left node is an index expr,
                # we can avoid creating another node and instead just add
                # the right node as a child of the left.
                left['children'].append(right)
                return self._node_span(left, self._span_of(left))
            else:
                return self._project_if_slice(left, right)
        else:
            # We have a projection
            self._match('star')
            self._match('rbracket')
            right = self._parse_projection_rhs(self.BINDING_POWER['star'])
            return self._node_span(ast.projection(left, right),
                                   self._span_of(left))

    def _project_if_slice(self, left, right):
        index_expr = self._node_span(ast.index_expression([left, right]),
                                     self._span_of(left))
        if right['type'] == 'slice':
            return self._node_span(
                ast.projection(
                    index_expr,
                    self._parse_projection_rhs(self.BINDING_POWER['star'])),
                self._span_of(index_expr))
        else:
            return index_expr

    def _parse_comparator(self, left, comparator):
        right = self._expression(self.BINDING_POWER[comparator])
        node = ast.comparator(comparator, left, right)
        return self._node_span(node, self._span_of(left))

    def _parse_multi_select_list(self):
        start = self._tokens[self._index - 1]['start']
        expressions = []
        while True:
            expression = self._expression()
            expressions.append(expression)
            if self._current_token() == 'rbracket':
                break
            else:
                self._match('comma')
        self._match('rbracket')
        return self._node_span(ast.multi_select_list(expressions), start)

    def _parse_multi_select_hash(self):
        start = self._tokens[self._index - 1]['start']
        pairs = []
        while True:
            key_token = self._lookahead_token(0)
            # Before getting the token value, verify it's
            # an identifier.
            self._match_multiple_tokens(
                token_types=['quoted_identifier', 'unquoted_identifier'])
            key_name = key_token['value']
            self._match('colon')
            value = self._expression(0)
            node = ast.key_val_pair(key_name=key_name, node=value)
            pairs.append(self._node_span(node, key_token['start']))
            if self._current_token() == 'comma':
                self._match('comma')
            elif self._current_token() == 'rbrace':
                self._match('rbrace')
                break
        return self._node_span(ast.multi_select_dict(nodes=pairs), start)

    def _parse_projection_rhs(self, binding_power):
        # Parse the right hand side of the projection.
        if self.BINDING_POWER[self._current_token()] < self._PROJECTION_STOP:
            # BP of 10 are all the tokens that stop a projection.
            right = self._identity_at(self._tokens[self._index - 1]['end'])
        elif self._current_token() == 'lbracket':
            right = self._expression(binding_power)
        elif self._current_token() == 'filter':
            right = self._expression(binding_power)
        elif self._current_token() == 'dot':
            self._match('dot')
            right = self._parse_dot_rhs(binding_power)
        else:
            self._raise_parse_error_for_token(self._lookahead_token(0),
                                              'syntax error')
        return right

    def _parse_dot_rhs(self, binding_power):
        # From the grammar:
        # expression '.' ( identifier /
        #                  multi-select-list /
        #                  multi-select-hash /
        #                  function-expression /
        #                  *
        # In terms of tokens that means that after a '.',
        # you can have:
        lookahead = self._current_token()
        # Common case "foo.bar", so first check for an identifier.
        if lookahead in ['quoted_identifier', 'unquoted_identifier', 'star']:
            return self._expression(binding_power)
        elif lookahead == 'lbracket':
            self._match('lbracket')
            return self._parse_multi_select_list()
        elif lookahead == 'lbrace':
            self._match('lbrace')
            return self._parse_multi_select_hash()
        else:
            t = self._lookahead_token(0)
            allowed = ['quoted_identifier', 'unquoted_identifier',
                       'lbracket', 'lbrace']
            msg = (
                "Expecting: %s, got: %s" % (allowed, t['type'])
            )
            self._raise_parse_error_for_token(t, msg)

    def _error_nud_token(self, token):
        if token['type'] == 'eof':
            raise exceptions.IncompleteExpressionError(
                token['start'], token['value'], token['type'])
        self._raise_parse_error_for_token(token, 'invalid token')

    def _error_led_token(self, token):
        self._raise_parse_error_for_token(token, 'invalid token')

    def _match(self, token_type=None):
        # inline'd self._current_token()
        if self._current_token() == token_type:
            # inline'd self._advance()
            self._advance()
        else:
            self._raise_parse_error_maybe_eof(
                token_type, self._lookahead_token(0))

    def _match_multiple_tokens(self, token_types):
        if self._current_token() not in token_types:
            self._raise_parse_error_maybe_eof(
                token_types, self._lookahead_token(0))
        self._advance()

    def _advance(self):
        self._index += 1

    def _current_token(self):
        return self._tokens[self._index]['type']

    def _lookahead(self, number):
        return self._tokens[self._index + number]['type']

    def _lookahead_token(self, number):
        return self._tokens[self._index + number]

    def _raise_parse_error_for_token(self, token, reason):
        lex_position = token['start']
        actual_value = token['value']
        actual_type = token['type']
        raise exceptions.ParseError(lex_position, actual_value,
                                    actual_type, reason)

    def _raise_parse_error_maybe_eof(self, expected_type, token):
        lex_position = token['start']
        actual_value = token['value']
        actual_type = token['type']
        if actual_type == 'eof':
            raise exceptions.IncompleteExpressionError(
                lex_position, actual_value, actual_type)
        message = 'Expecting: %s, got: %s' % (expected_type,
                                              actual_type)
        raise exceptions.ParseError(
            lex_position, actual_value, actual_type, message)

    @classmethod
    def purge(cls):
        """Clear the expression compilation cache."""
        cls._CACHE.clear()


@with_repr_method
class ParsedResult(object):
    def __init__(self, expression, parsed):
        self.expression = expression
        self.parsed = parsed

    def search(self, value, options=None):
        interpreter = visitor.TreeInterpreter(options)
        try:
            result = interpreter.visit(self.parsed, value)
        except exceptions.JMESPathError as e:
            if e.expression is None:
                # Give runtime errors access to the original
                # expression so they can point at the failing
                # segment when position tracking is enabled.
                e.expression = self.expression
            raise
        return result

    def _render_dot_file(self):
        """Render the parsed AST as a dot file.

        Note that this is marked as an internal method because
        the AST is an implementation detail and is subject
        to change.  This method can be used to help troubleshoot
        or for development purposes, but is not considered part
        of the public supported API.  Use at your own risk.

        """
        renderer = visitor.GraphvizVisitor()
        contents = renderer.visit(self.parsed)
        return contents

    def __repr__(self):
        return repr(self.parsed)
