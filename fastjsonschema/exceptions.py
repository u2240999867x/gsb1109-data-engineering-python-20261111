import re
from typing import Any


SPLIT_RE = re.compile(r'[\.\[\]]+')


class JsonSchemaException(ValueError):
    """
    Base exception of ``fastjsonschema`` library.
    """


class JsonSchemaValueException(JsonSchemaException):
    """
    Exception raised by validation function. Available properties:

     * ``message`` containing human-readable information what is wrong
       (e.g. ``data.property[index] must be smaller than or equal to 42``),
     * invalid ``value`` (e.g. ``60``),
     * ``name`` of a path in the data structure (e.g. ``data.property[index]``),
     * ``path`` as an array in the data structure (e.g. ``['data', 'property', 'index']``),
     * the whole ``definition`` which the ``value`` has to fulfil (e.g. ``{'type': 'number', 'maximum': 42}``),
     * ``rule`` which the ``value`` is breaking (e.g. ``maximum``)
     * and ``rule_definition`` (e.g. ``42``).

    .. versionchanged:: 2.14.0
        Added all extra properties.
    """

    def __init__(
        self,
        message: str,
        value: Any = None,
        name: str | None = None,
        definition: Any = None,
        rule: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.value = value
        self.name = name
        self.definition = definition
        self.rule = rule

    @property
    def path(self) -> list[str]:
        return [item for item in SPLIT_RE.split(self.name or '') if item != '']

    @property
    def rule_definition(self) -> Any:
        if not self.rule or not self.definition:
            return None
        return self.definition.get(self.rule)


class ErrorBudget:
    """
    Shared counter limiting how many errors are collected during one whole
    validation run when ``fast_fail`` is turned off.

    A single budget is passed down through nested validation and through the
    separate functions generated for ``$ref`` targets, so every part of the
    document draws from the same limit. Once the limit is reached no further
    error objects are created and loops stop walking the remaining data, which
    bounds both memory and work for huge invalid arrays instead of collecting
    every error first and truncating the list afterwards.

    A budget with ``limit=None`` never stops, preserving the "collect all
    errors" behavior used when no error limit is configured.
    """

    __slots__ = ('limit', 'collected', 'overflowed', 'exhausted_locations')

    def __init__(self, limit: int | None = None) -> None:
        self.limit = limit
        self.collected = 0
        # True once an error had to be dropped or part of the document was not
        # walked because the limit was reached. Merely reaching the limit with
        # nothing left unchecked does not set it, so an exact-fit run is not
        # reported as truncated.
        self.overflowed = False
        # Names of the data locations (e.g. ``data[3]``) whose validation was
        # stopped because the budget ran out while they were being checked.
        self.exhausted_locations: list[str] = []

    @property
    def is_exhausted(self) -> bool:
        return self.limit is not None and self.collected >= self.limit

    def record(self) -> bool:
        """
        Reserve one slot for an error. Returns ``False`` when the budget is
        already spent, so the caller must not create the error object.
        """
        if self.is_exhausted:
            self.overflowed = True
            return False
        self.collected += 1
        return True

    def note_exhausted(self, location: str) -> None:
        self.overflowed = True
        if location not in self.exhausted_locations:
            self.exhausted_locations.append(location)


class JsonSchemaValuesException(JsonSchemaException):
    """
    Exception raised by validation function when ``fast_fail`` is turned off.
    Available properties:

     * ``errors`` containing all :any:`JsonSchemaValueException` found in the data
     * and ``message`` joining messages of all the errors.

    When an error budget (``max_errors``) is configured, ``errors`` holds at
    most that many items, ``truncated`` is ``True`` if the budget ran out
    before the whole document was checked, ``total_errors`` is the number of
    errors kept (further errors were not collected, so it is not a count of
    every error in the data), and ``unvalidated`` lists the data locations that
    were not fully checked.
    """

    def __init__(
        self,
        errors: list[JsonSchemaValueException],
        truncated: bool = False,
        unvalidated: list[str] | None = None,
    ) -> None:
        self.message = '; '.join(error.message for error in errors)
        if truncated:
            self.message += '; ... (error budget exhausted, further errors were not collected)'
        super().__init__(self.message)
        self.errors = errors
        self.truncated = truncated
        self.unvalidated = unvalidated or []

    @property
    def total_errors(self) -> int:
        return len(self.errors)


class JsonSchemaDefinitionException(JsonSchemaException):
    """
    Exception raised by generator of validation function.
    """
