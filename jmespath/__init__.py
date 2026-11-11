from jmespath import parser
from jmespath.visitor import Options

__version__ = '1.1.0'


def compile(expression, track_positions=False):
    return parser.Parser(track_positions=track_positions).parse(expression)


def search(expression, data, options=None):
    return parser.Parser().parse(expression).search(data, options=options)
