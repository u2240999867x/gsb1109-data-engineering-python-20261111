from collections.abc import Sequence
from typing import Any, Optional, Union

from typing_extensions import Self

from cattrs._compat import ExceptionGroup


class CattrsError(Exception):
    """Base ``cattrs`` exception."""


class StructureHandlerNotFoundError(CattrsError):
    """
    Error raised when structuring cannot find a handler for converting inputs into
    :attr:`type_`.
    """

    def __init__(self, message: str, type_: type) -> None:
        super().__init__(message, type_)
        self.message = message
        self.type_ = type_

    def __str__(self) -> str:
        return self.message


class BaseValidationError(ExceptionGroup, CattrsError):
    cl: type

    def __new__(cls, message: str, excs: Sequence[Exception], cl: type) -> Self:
        obj = super().__new__(cls, message, excs)
        obj.cl = cl
        return obj

    def derive(self, excs: Sequence[Exception]) -> Self:
        return self.__class__(self.message, excs, self.cl)


class IterableValidationNote(str):
    """Attached as a note to an exception when an iterable element fails structuring."""

    index: Union[int, str]  # Ints for list indices, strs for dict keys
    type: Any

    def __new__(cls, string: str, index: Union[int, str], type: Any) -> Self:
        instance = str.__new__(cls, string)
        instance.index = index
        instance.type = type
        return instance

    def __getnewargs__(self) -> tuple[str, Union[int, str], Any]:
        return (str(self), self.index, self.type)


class IterableValidationError(BaseValidationError):
    """Raised when structuring an iterable."""

    def group_exceptions(
        self,
    ) -> tuple[list[tuple[Exception, IterableValidationNote]], list[Exception]]:
        """Split the exceptions into two groups: with and without validation notes."""
        excs_with_notes = []
        other_excs = []
        for subexc in self.exceptions:
            if hasattr(subexc, "__notes__"):
                for note in subexc.__notes__:
                    if note.__class__ is IterableValidationNote:
                        excs_with_notes.append((subexc, note))
                        break
                else:
                    other_excs.append(subexc)
            else:
                other_excs.append(subexc)

        return excs_with_notes, other_excs


class AttributeValidationNote(str):
    """Attached as a note to an exception when an attribute fails structuring."""

    name: str
    type: Any

    def __new__(cls, string: str, name: str, type: Any) -> Self:
        instance = str.__new__(cls, string)
        instance.name = name
        instance.type = type
        return instance

    def __getnewargs__(self) -> tuple[str, str, Any]:
        return (str(self), self.name, self.type)


class ClassValidationError(BaseValidationError):
    """Raised when validating a class if any attributes are invalid."""

    def group_exceptions(
        self,
    ) -> tuple[list[tuple[Exception, AttributeValidationNote]], list[Exception]]:
        """Split the exceptions into two groups: with and without validation notes."""
        excs_with_notes = []
        other_excs = []
        for subexc in self.exceptions:
            if hasattr(subexc, "__notes__"):
                for note in subexc.__notes__:
                    if note.__class__ is AttributeValidationNote:
                        excs_with_notes.append((subexc, note))
                        break
                else:
                    other_excs.append(subexc)
            else:
                other_excs.append(subexc)

        return excs_with_notes, other_excs


class ForbiddenExtraKeysError(CattrsError):
    """
    Raised when `forbid_extra_keys` is activated and such extra keys are detected
    during structuring.

    The attribute `extra_fields` is a sequence of those extra keys, which were the
    cause of this error, and `cl` is the class which was structured with those extra
    keys.
    """

    def __init__(
        self, message: Optional[str], cl: type, extra_fields: set[str]
    ) -> None:
        self.message = message
        self.cl = cl
        self.extra_fields = extra_fields

        super().__init__(message, cl, extra_fields)

    def __str__(self) -> str:
        return (
            self.message
            or f"Extra fields in constructor for {self.cl.__name__}: "
            f"{', '.join(sorted(self.extra_fields))}"
        )


class UnionDisambiguationError(CattrsError):
    """Base class for errors raised while disambiguating a union without
    trying the member structure hooks.

    :param union: The union that could not be disambiguated.
    :param candidates: The union members still considered when the error was
        raised.
    :param payload: The raw payload that could not be disambiguated.

    .. versionadded:: NEXT
    """

    def __init__(self, union: Any, candidates: tuple[Any, ...], payload: Any) -> None:
        self.union = union
        self.candidates = tuple(candidates)
        self.payload = payload
        super().__init__(union, self.candidates, payload)

    @staticmethod
    def _format_candidates(candidates: tuple[Any, ...]) -> str:
        return ", ".join(getattr(c, "__name__", repr(c)) for c in candidates)


class UnionTagMissingError(UnionDisambiguationError):
    """Raised when a payload carries none of the declared tag values.

    This is distinct from a tag conflict: the payload does not contain a tag
    value matching any union member, rather than containing several of them.

    .. versionadded:: NEXT
    """

    def __init__(
        self,
        union: Any,
        candidates: tuple[Any, ...],
        payload: Any,
        tag_values: tuple[Any, ...],
    ) -> None:
        self.tag_values = tuple(tag_values)
        super().__init__(union, candidates, payload)

    def __str__(self) -> str:
        return (
            f"No union tag found for {self.union}; expected one of "
            f"{list(self.tag_values)} among candidates "
            f"{self._format_candidates(self.candidates)}"
        )


class UnionTagConflictError(UnionDisambiguationError):
    """Raised when a payload matches the tag values of more than one member.

    A conflict means the payload contains *several* declared tags at once;
    a missing tag raises :class:`UnionTagMissingError` instead.

    :param matches: The tag values present in the payload, paired with the
        members declaring them.

    .. versionadded:: NEXT
    """

    def __init__(
        self,
        union: Any,
        candidates: tuple[Any, ...],
        payload: Any,
        matches: tuple[tuple[Any, Any], ...],
    ) -> None:
        self.matches = tuple(matches)
        super().__init__(union, candidates, payload)

    def __str__(self) -> str:
        rendered = ", ".join(
            f"{value!r} -> {member.__name__}" for value, member in self.matches
        )
        return (
            f"Conflicting union tags in payload for {self.union}: {rendered}; "
            f"candidates narrowed to {self._format_candidates(self.candidates)}"
        )


class UnionTagUnknownError(UnionDisambiguationError):
    """Raised when a tag field is present but its value matches no member.

    .. versionadded:: NEXT
    """

    def __init__(
        self,
        union: Any,
        candidates: tuple[Any, ...],
        payload: Any,
        tag_name: str,
        tag_value: Any,
    ) -> None:
        self.tag_name = tag_name
        self.tag_value = tag_value
        super().__init__(union, candidates, payload)

    def __str__(self) -> str:
        return (
            f"Unknown union tag {self.tag_value!r} in field {self.tag_name!r} "
            f"for {self.union}; candidates: "
            f"{self._format_candidates(self.candidates)}"
        )


class AmbiguousUnionError(UnionDisambiguationError):
    """Raised when the declared markers leave more than one candidate.

    The markers could be examined without side effects, but they do not identify
    a single union member, so no member structure hook is invoked.

    .. versionadded:: NEXT
    """

    def __str__(self) -> str:
        return (
            f"Could not uniquely disambiguate {self.union}; remaining candidates: "
            f"{self._format_candidates(self.candidates)}"
        )
