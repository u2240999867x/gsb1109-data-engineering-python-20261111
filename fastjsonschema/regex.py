"""
Controlled regular expression execution strategies.

Schemas are often provided by external parties, and a ``pattern`` or
``patternProperties`` regular expression can take a very long time even on
short strings (catastrophic backtracking). Callers can therefore choose how
regular expressions used by ``pattern`` and ``patternProperties`` are executed:

* :data:`REGEX_STRATEGY_STDLIB` - the default :mod:`re` engine, unchanged
  behavior and no time limit;
* :data:`REGEX_STRATEGY_REGEX` - the third-party :mod:`regex` engine with a
  per-match time limit. When the engine aborts a match, the failure is reported
  as :class:`~fastjsonschema.exceptions.JsonSchemaRegexException`, never as an
  ordinary pattern mismatch.

Only ``pattern`` and ``patternProperties`` are covered by the strategy. Format
regular expressions ship with this library and are not attacker controlled.
"""
from dataclasses import dataclass
import re
from typing import Any

from .exceptions import JsonSchemaDefinitionException, JsonSchemaRegexException

REGEX_STRATEGY_STDLIB = 'stdlib'
REGEX_STRATEGY_REGEX = 'regex'

REGEX_STRATEGIES = (REGEX_STRATEGY_STDLIB, REGEX_STRATEGY_REGEX)

# Default per-match time limit (in seconds) for the ``regex`` strategy.
DEFAULT_REGEX_TIMEOUT = 0.1


@dataclass(frozen=True)
class RegexStrategy:
    """
    Description of a regular expression execution strategy.

    Instances are immutable so the choice made at compile time stays visible
    both on the compiled validator and inside the generated standalone code.
    """

    name: str
    timeout: float | None = None

    def __post_init__(self) -> None:
        if self.name not in REGEX_STRATEGIES:
            raise JsonSchemaDefinitionException(
                'Unknown regex strategy {!r}; choose one of {}'.format(
                    self.name, ', '.join(repr(name) for name in REGEX_STRATEGIES)
                )
            )
        if self.name == REGEX_STRATEGY_REGEX:
            timeout = DEFAULT_REGEX_TIMEOUT if self.timeout is None else self.timeout
            if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
                raise JsonSchemaDefinitionException(
                    'Regex strategy timeout must be a positive number of seconds, got {!r}'.format(self.timeout)
                )
            object.__setattr__(self, 'timeout', float(timeout))
        elif self.timeout is not None:
            raise JsonSchemaDefinitionException(
                'Regex strategy {!r} does not support a timeout'.format(self.name)
            )

    @property
    def module_name(self) -> str:
        return 're' if self.name == REGEX_STRATEGY_STDLIB else 'regex'

    @property
    def engine(self) -> Any:
        if self.name == REGEX_STRATEGY_STDLIB:
            return re
        try:
            import regex
        except ImportError as exc:
            raise JsonSchemaDefinitionException(
                "Regex strategy 'regex' requires the 'regex' package; install it with 'pip install regex'"
            ) from exc
        return regex

    @property
    def error_type(self) -> type[Exception]:
        return self.engine.error

    def compile(self, pattern: str) -> Any:
        return self.engine.compile(pattern)


def get_regex_strategy(strategy: str | RegexStrategy | None, timeout: float | None = None) -> RegexStrategy:
    """
    Normalize a user supplied strategy argument into a :class:`RegexStrategy`.
    """
    if strategy is None:
        strategy = REGEX_STRATEGY_STDLIB
    if isinstance(strategy, RegexStrategy):
        if timeout is not None and timeout != strategy.timeout:
            raise JsonSchemaDefinitionException(
                'Cannot pass both a RegexStrategy and a different timeout'
            )
        return strategy
    return RegexStrategy(strategy, timeout)
