import math
import json
from decimal import Decimal

from jmespath import exceptions
from jmespath import numeric
from jmespath.compat import string_type as STRING_TYPE
from jmespath.compat import get_methods


# python types -> jmespath types
TYPES_MAP = {
    'bool': 'boolean',
    'list': 'array',
    'dict': 'object',
    'NoneType': 'null',
    'unicode': 'string',
    'str': 'string',
    'float': 'number',
    'int': 'number',
    'long': 'number',
    'Decimal': 'number',
    'OrderedDict': 'object',
    '_Projection': 'array',
    '_Expression': 'expref',
}


# jmespath types -> python types
REVERSE_TYPES_MAP = {
    'boolean': ('bool',),
    'array': ('list', '_Projection'),
    'object': ('dict', 'OrderedDict',),
    'null': ('NoneType',),
    'string': ('unicode', 'str'),
    'number': ('float', 'int', 'long', 'Decimal'),
    'expref': ('_Expression',),
}


def signature(*arguments):
    def _record_signature(func):
        func.signature = arguments
        return func
    return _record_signature


def _json_key(key):
    # Mirror ``json.dumps`` dict-key handling so non-string keys keep
    # their historical quoted representation.
    if isinstance(key, str):
        text = key
    elif key is True:
        text = 'true'
    elif key is False:
        text = 'false'
    elif key is None:
        text = 'null'
    else:
        text = str(key)
    return json.dumps(text, separators=(',', ':'), default=str)


def _json_dumps(value):
    """Serialize ``value`` to JSON without turning Decimals into strings.

    ``json.dumps`` has no native ``Decimal`` support, so the default
    ``str`` fallback would quote a Decimal (``"1.5"``) even though an
    equivalent float renders as a number (``1.5``).  Render Decimals as
    exact JSON number literals instead, matching the historical float
    output while preserving the Decimal's precision and scale.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        items = [
            '%s:%s' % (_json_key(key), _json_dumps(item))
            for key, item in value.items()]
        return '{%s}' % ','.join(items)
    if isinstance(value, (list, tuple)):
        return '[%s]' % ','.join(_json_dumps(item) for item in value)
    return json.dumps(value, separators=(',', ':'), default=str)


class FunctionRegistry(type):
    def __init__(cls, name, bases, attrs):
        cls._populate_function_table()
        super(FunctionRegistry, cls).__init__(name, bases, attrs)

    def _populate_function_table(cls):
        function_table = {}
        # Any method with a @signature decorator that also
        # starts with "_func_" is registered as a function.
        # _func_max_by -> max_by function.
        for name, method in get_methods(cls):
            if not name.startswith('_func_'):
                continue
            signature = getattr(method, 'signature', None)
            if signature is not None:
                function_table[name[6:]] = {
                    'function': method,
                    'signature': signature,
                }
        cls.FUNCTION_TABLE = function_table


class Functions(metaclass=FunctionRegistry):

    FUNCTION_TABLE = {
    }

    def call_function(self, function_name, resolved_args):
        try:
            spec = self.FUNCTION_TABLE[function_name]
        except KeyError:
            raise exceptions.UnknownFunctionError(
                "Unknown function: %s()" % function_name)
        function = spec['function']
        signature = spec['signature']
        self._validate_arguments(resolved_args, signature, function_name)
        return function(self, *resolved_args)

    def _validate_arguments(self, args, signature, function_name):
        if signature and signature[-1].get('variadic'):
            if len(args) < len(signature):
                raise exceptions.VariadictArityError(
                    len(signature), len(args), function_name)
        elif len(args) != len(signature):
            raise exceptions.ArityError(
                len(signature), len(args), function_name)
        return self._type_check(args, signature, function_name)

    def _type_check(self, actual, signature, function_name):
        for i in range(len(signature)):
            allowed_types = signature[i]['types']
            if allowed_types:
                self._type_check_single(actual[i], allowed_types,
                                        function_name)

    def _type_check_single(self, current, types, function_name):
        # Type checking involves checking the top level type,
        # and in the case of arrays, potentially checking the types
        # of each element.
        allowed_types, allowed_subtypes = self._get_allowed_pytypes(types)
        # We're not using isinstance() on purpose.
        # The type model for jmespath does not map
        # 1-1 with python types (booleans are considered
        # integers in python for example).
        actual_typename = type(current).__name__
        if actual_typename not in allowed_types:
            raise exceptions.JMESPathTypeError(
                function_name, current,
                self._convert_to_jmespath_type(actual_typename), types)
        # If we're dealing with a list type, we can have
        # additional restrictions on the type of the list
        # elements (for example a function can require a
        # list of numbers or a list of strings).
        # Arrays are the only types that can have subtypes.
        if allowed_subtypes:
            self._subtype_check(current, allowed_subtypes,
                                types, function_name)

    def _get_allowed_pytypes(self, types):
        allowed_types = []
        allowed_subtypes = []
        for t in types:
            type_ = t.split('-', 1)
            if len(type_) == 2:
                type_, subtype = type_
                allowed_subtypes.append(REVERSE_TYPES_MAP[subtype])
            else:
                type_ = type_[0]
            allowed_types.extend(REVERSE_TYPES_MAP[type_])
        return allowed_types, allowed_subtypes

    def _subtype_check(self, current, allowed_subtypes, types, function_name):
        if len(allowed_subtypes) == 1:
            # The easy case, we know up front what type
            # we need to validate.
            allowed_subtypes = allowed_subtypes[0]
            for element in current:
                actual_typename = type(element).__name__
                if actual_typename not in allowed_subtypes:
                    raise exceptions.JMESPathTypeError(
                        function_name, element, actual_typename, types)
        elif len(allowed_subtypes) > 1 and current:
            # Dynamic type validation.  Based on the first
            # type we see, we validate that the remaining types
            # match.
            first = type(current[0]).__name__
            for subtypes in allowed_subtypes:
                if first in subtypes:
                    allowed = subtypes
                    break
            else:
                raise exceptions.JMESPathTypeError(
                    function_name, current[0], first, types)
            for element in current:
                actual_typename = type(element).__name__
                if actual_typename not in allowed:
                    raise exceptions.JMESPathTypeError(
                        function_name, element, actual_typename, types)

    @signature({'types': ['number']})
    def _func_abs(self, arg):
        return abs(arg)

    @signature({'types': ['array-number']})
    def _func_avg(self, arg):
        if arg:
            numeric.check_numeric_values('avg', 1, arg)
            total = sum(arg)
            if isinstance(total, Decimal):
                # Keep Decimal precision: dividing by a float would
                # coerce the whole result to binary float.
                return total / Decimal(len(arg))
            else:
                return total / len(arg)
        else:
            return None

    @signature({'types': [], 'variadic': True})
    def _func_not_null(self, *arguments):
        for argument in arguments:
            if argument is not None:
                return argument

    @signature({'types': []})
    def _func_to_array(self, arg):
        if isinstance(arg, list):
            return arg
        else:
            return [arg]

    @signature({'types': []})
    def _func_to_string(self, arg):
        if isinstance(arg, STRING_TYPE):
            return arg
        else:
            return _json_dumps(arg)

    @signature({'types': []})
    def _func_to_number(self, arg):
        if isinstance(arg, (list, dict, bool)):
            return None
        elif arg is None:
            return None
        elif isinstance(arg, (int, float, Decimal)):
            # Decimal is returned untouched: converting it to float would
            # silently lose the precision the caller parsed it with.
            return arg
        else:
            try:
                return int(arg)
            except ValueError:
                try:
                    return float(arg)
                except ValueError:
                    return None

    @signature({'types': ['array', 'string']}, {'types': []})
    def _func_contains(self, subject, search):
        return search in subject

    @signature({'types': ['string', 'array', 'object']})
    def _func_length(self, arg):
        return len(arg)

    @signature({'types': ['string']}, {'types': ['string']})
    def _func_ends_with(self, search, suffix):
        return search.endswith(suffix)

    @signature({'types': ['string']}, {'types': ['string']})
    def _func_starts_with(self, search, suffix):
        return search.startswith(suffix)

    @signature({'types': ['array', 'string']})
    def _func_reverse(self, arg):
        if isinstance(arg, STRING_TYPE):
            return arg[::-1]
        else:
            return list(reversed(arg))

    @signature({"types": ['number']})
    def _func_ceil(self, arg):
        return math.ceil(arg)

    @signature({"types": ['number']})
    def _func_floor(self, arg):
        return math.floor(arg)

    @signature({"types": ['string']}, {"types": ['array-string']})
    def _func_join(self, separator, array):
        return separator.join(array)

    @signature({'types': ['expref']}, {'types': ['array']})
    def _func_map(self, expref, arg):
        result = []
        for element in arg:
            result.append(expref.visit(expref.expression, element))
        return result

    @signature({"types": ['array-number', 'array-string']})
    def _func_max(self, arg):
        if arg:
            numeric.check_numeric_values('max', 1, arg)
            return max(arg)
        else:
            return None

    @signature({"types": ["object"], "variadic": True})
    def _func_merge(self, *arguments):
        merged = {}
        for arg in arguments:
            merged.update(arg)
        return merged

    @signature({"types": ['array-number', 'array-string']})
    def _func_min(self, arg):
        if arg:
            numeric.check_numeric_values('min', 1, arg)
            return min(arg)
        else:
            return None

    @signature({"types": ['array-string', 'array-number']})
    def _func_sort(self, arg):
        numeric.check_numeric_values('sort', 1, arg)
        return list(sorted(arg))

    @signature({"types": ['array-number']})
    def _func_sum(self, arg):
        numeric.check_numeric_values('sum', 1, arg)
        return sum(arg)

    @signature({"types": ['object']})
    def _func_keys(self, arg):
        # To be consistent with .values()
        # should we also return the indices of a list?
        return list(arg.keys())

    @signature({"types": ['object']})
    def _func_values(self, arg):
        return list(arg.values())

    @signature({'types': []})
    def _func_type(self, arg):
        if isinstance(arg, STRING_TYPE):
            return "string"
        elif isinstance(arg, bool):
            return "boolean"
        elif isinstance(arg, list):
            return "array"
        elif isinstance(arg, dict):
            return "object"
        elif isinstance(arg, (float, int, Decimal)):
            return "number"
        elif arg is None:
            return "null"

    @signature({'types': ['array']}, {'types': ['expref']})
    def _func_sort_by(self, array, expref):
        if not array:
            return array
        # sort_by allows for the expref to be either a number of
        # a string, so we have some special logic to handle this.
        # We evaluate the first array element and verify that it's
        # either a string of a number.  We then create a key function
        # that validates that type, which requires that remaining array
        # elements resolve to the same type as the first element.
        required_type = self._convert_to_jmespath_type(
            type(expref.visit(expref.expression, array[0])).__name__)
        if required_type not in ['number', 'string']:
            raise exceptions.JMESPathTypeError(
                'sort_by', array[0], required_type, ['string', 'number'])
        self._validate_key_values('sort_by', expref, array, required_type)
        keyfunc = self._create_key_func(expref,
                                        [required_type],
                                        'sort_by')
        return list(sorted(array, key=keyfunc))

    @signature({'types': ['array']}, {'types': ['expref']})
    def _func_min_by(self, array, expref):
        self._validate_key_values(
            'min_by', expref, array, ['number', 'string'])
        keyfunc = self._create_key_func(expref,
                                        ['number', 'string'],
                                        'min_by')
        if array:
            return min(array, key=keyfunc)
        else:
            return None

    @signature({'types': ['array']}, {'types': ['expref']})
    def _func_max_by(self, array, expref):
        self._validate_key_values(
            'max_by', expref, array, ['number', 'string'])
        keyfunc = self._create_key_func(expref,
                                        ['number', 'string'],
                                        'max_by')
        if array:
            return max(array, key=keyfunc)
        else:
            return None

    def _validate_key_values(self, function_name, expref, array,
                             allowed_types):
        # sort_by validates against a single type (a plain string) while
        # min_by/max_by accept both numbers and strings (a list).
        if isinstance(allowed_types, str):
            allowed_types = [allowed_types]
        # Evaluate every key up front so that the type error is always
        # attributed to this function (and, for numeric keys, so a
        # Decimal/float mix is rejected before sorting can coerce).
        key_values = [
            expref.visit(expref.expression, element) for element in array]
        if not key_values:
            return
        resolved_types = [
            self._convert_to_jmespath_type(type(value).__name__)
            for value in key_values]
        for value, jmespath_type in zip(key_values, resolved_types):
            if jmespath_type not in allowed_types:
                raise exceptions.JMESPathTypeError(
                    function_name, value, jmespath_type,
                    allowed_types)
        # When numbers and strings are both allowed, the keys still have
        # to be homogeneous because a number key cannot be ordered against
        # a string key.  This mirrors sort_by's dynamic key validation.
        if 'number' in allowed_types and 'string' in allowed_types:
            required_type = resolved_types[0]
            for value, jmespath_type in zip(key_values[1:],
                                            resolved_types[1:]):
                if jmespath_type != required_type:
                    raise exceptions.JMESPathTypeError(
                        function_name, value, jmespath_type,
                        [required_type])
        if resolved_types[0] == 'number':
            numeric.check_numeric_values(function_name, 2, key_values)

    def _create_key_func(self, expref, allowed_types, function_name):
        def keyfunc(x):
            result = expref.visit(expref.expression, x)
            actual_typename = type(result).__name__
            jmespath_type = self._convert_to_jmespath_type(actual_typename)
            # allowed_types is in term of jmespath types, not python types.
            if jmespath_type not in allowed_types:
                raise exceptions.JMESPathTypeError(
                    function_name, result, jmespath_type, allowed_types)
            return result
        return keyfunc

    def _convert_to_jmespath_type(self, pyobject):
        return TYPES_MAP.get(pyobject, 'unknown')
