from jmespath import parser
from jmespath.visitor import Options
from jmespath.missing import MISSING

__version__ = '1.1.0'


def compile(expression):
    return parser.Parser().parse(expression)


def search(expression, data, options=None):
    return parser.Parser().parse(expression).search(data, options=options)


def inspect(expression, data, options=None):
    """Evaluate an expression in data inspection mode.

    This behaves like search(), except that accessing a key that is
    absent from an object evaluates to the jmespath.MISSING sentinel
    instead of None.  This lets you distinguish "the field was not
    present" from "the field was present with a null value", both in
    the returned result and within the expression itself (for example
    via the is_missing() function or type() == '"missing"').

    Any dict_cls/custom_functions from the provided options are kept.
    """
    if options is None:
        options = Options()
    inspect_options = Options(
        dict_cls=options.dict_cls,
        custom_functions=options.custom_functions,
        preserve_missing=True)
    return parser.Parser().parse(expression).search(
        data, options=inspect_options)
