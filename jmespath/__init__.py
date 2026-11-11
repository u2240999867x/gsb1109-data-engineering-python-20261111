from jmespath import parser
from jmespath.visitor import Options
from jmespath.exceptions import UndefinedParameterError

__version__ = '1.1.0'


def compile(expression):
    return parser.Parser().parse(expression)


def search(expression, data, options=None, params=None):
    return parser.Parser().parse(expression).search(
        data, options=options, params=params)
