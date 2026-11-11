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


class JsonSchemaValuesException(JsonSchemaException):
    """
    Exception raised by validation function when ``fast_fail`` is turned off.
    Available properties:

     * ``errors`` containing all :any:`JsonSchemaValueException` found in the data
     * and ``message`` joining messages of all the errors.
    """

    def __init__(self, errors: list[JsonSchemaValueException]) -> None:
        self.message = '; '.join(error.message for error in errors)
        super().__init__(self.message)
        self.errors = errors


class JsonSchemaDefinitionException(JsonSchemaException):
    """
    Exception raised by generator of validation function.
    """


class RefRetrievalException(JsonSchemaDefinitionException):
    """
    Base exception for problems while resolving a remote ``$ref``.

    Every subclass exposes the affected ``uri`` and a machine-readable
    ``reason`` so callers can tell a policy rejection apart from a network
    failure or a content conflict without parsing error messages.
    """

    def __init__(self, message: str, *, uri: str, reason: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.uri = uri
        self.reason = reason
        for name, value in details.items():
            setattr(self, name, value)


class RefPolicyDenied(RefRetrievalException):
    """
    A remote reference was rejected by the call-level ``RefPolicy``.

    Possible reasons include ``scheme_not_allowed``, ``uri_not_allowed``,
    ``redirect_not_allowed``, ``too_many_redirects``,
    ``response_too_large``, ``resource_id_not_allowed`` and
    ``resource_id_mismatch``.
    """


class RefFetchError(RefRetrievalException):
    """
    A reference allowed by the policy could not be retrieved or decoded.

    Typical reasons are ``network_error``, ``invalid_json``,
    ``handler_error`` and ``fetch_failed``. The original exception is
    preserved as ``__cause__``.
    """


class RefContentConflict(RefRetrievalException):
    """
    The same URI was resolved to documents with different content.

    Raised with reason ``content_conflict`` when a URI is fetched again and
    the new document differs from the one previously retrieved, or with
    reason ``resource_id_conflict`` when a document claims an identity
    (``$id``/``id``) already bound to a different document.
    """
