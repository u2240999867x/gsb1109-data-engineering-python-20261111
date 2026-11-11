"""Numeric type policy for JMESPath values.

Upstream data (for example money amounts parsed with
``json.loads(..., parse_float=decimal.Decimal)`` can reach the interpreter
as :class:`decimal.Decimal` values.  JMESPath treats those as ordinary
numbers, but ``Decimal`` and binary ``float`` do not share the same
precision guarantees, so mixing them needs an explicit policy rather than
relying on Python's implicit coercion rules.

The policy enforced by this module is:

1. ``int`` (and any other exact :class:`numbers.Integral` value) is
   compatible with both ``Decimal`` and ``float``.  Integer arithmetic
   never loses precision, so ``Decimal + int`` stays a ``Decimal`` and
   ``float + int`` stays a ``float``.
2. ``Decimal`` and ``float`` must never be combined inside one JMESPath
   numeric operation (``sum``, ``avg``, ``min``, ``max``, ``sort``,
   ``sort_by``/``min_by``/``max_by`` keys or an ordering comparator).
   JMESPath never implicitly converts a ``Decimal`` to ``float`` because
   that would silently drop precision.  Such a call fails loudly with a
   :class:`jmespath.exceptions.JMESPathNumericTypeError` that names the
   function and the argument position containing the incompatible value.
3. Plain JSON numbers (``int``/``float``) keep their historical behavior;
   nothing is coerced when no ``Decimal`` is involved.
4. ``bool`` is never a number even though it subclasses ``int``.

Equality (``==``/``!=``) performs no arithmetic and never coerces either
operand, so it is not covered by the mixing rule; only operations that
order or aggregate numbers are guarded.
"""
from decimal import Decimal

from numbers import Number

from jmespath import exceptions


# Sentinel categories returned by :func:`numeric_kind`.
DECIMAL_KIND = 'decimal'
JSON_NUMBER_KIND = 'json-number'


def is_number(value):
    # ``Number`` covers int, float and Decimal.  Booleans are excluded
    # because JMESPath models them as a separate type even though
    # ``bool`` subclasses ``int``.
    return not isinstance(value, bool) and isinstance(value, Number)


def numeric_kind(value):
    """Return the numeric policy kind of ``value``.

    Returns :data:`DECIMAL_KIND` for ``Decimal``,
    :data:`JSON_NUMBER_KIND` for ordinary int/float numbers and ``None``
    for booleans and any non-numeric value.
    """
    if not is_number(value):
        return None
    if isinstance(value, Decimal):
        return DECIMAL_KIND
    return JSON_NUMBER_KIND


def check_numeric_values(function_name, argument_index, values):
    """Validate that ``values`` never mix ``Decimal`` with ``float``.

    ``values`` must already have been type-checked as numbers.  Integers
    are accepted alongside either kind.  The first element that creates
    a Decimal/float mix raises :class:`JMESPathNumericTypeError` so the
    reported value is the offending argument rather than a later element.
    """
    has_decimal = False
    has_float = False
    for value in values:
        if isinstance(value, Decimal):
            has_decimal = True
        elif isinstance(value, float):
            has_float = True
        if has_decimal and has_float:
            raise exceptions.JMESPathNumericTypeError.for_function(
                function_name, argument_index, value)


def check_numeric_operands(operator, left, right):
    """Validate an ordering comparison between two numbers.

    ``int`` is compatible with both ``Decimal`` and ``float``.  Only a
    direct ``Decimal``/``float`` pair is rejected, naming the operand
    position that introduces the incompatible type.
    """
    if isinstance(left, Decimal) and isinstance(right, float):
        raise exceptions.JMESPathNumericTypeError.for_operator(
            operator, 1, left)
    if isinstance(left, float) and isinstance(right, Decimal):
        raise exceptions.JMESPathNumericTypeError.for_operator(
            operator, 2, right)
