from __future__ import annotations

from threading import local
from typing import Any, Callable

from attrs import frozen


@frozen
class AttributeOverride:
    omit_if_default: bool | None = None
    rename: str | None = None
    omit: bool | None = None  # Omit the field completely.
    struct_hook: Callable[[Any, Any], Any] | None = None  # Structure hook to use.
    unstruct_hook: Callable[[Any], Any] | None = None  # Structure hook to use.


@frozen
class Extras:
    """Declare how a class retains unrecognized fields.

    Attach an instance to an _attrs_ class or dataclass as the ``__cattrs_extras__``
    class attribute to opt in to explicit extra-field retention::

        @define
        class Record:
            name: str
            extra: dict[str, Any] = Factory(dict)

            __cattrs_extras__ = Extras(field="extra")

    Exactly one declaration target is supported:

    * ``Extras(field="extra")``: input keys that do not correspond to a declared
      field are collected into a mapping and stored on the ``extra`` attribute.
      When unstructuring, the mapping's items are merged back into the output
      dictionary at the top level, and the ``extra`` attribute itself is not
      emitted under its own name.
    * ``Extras(key="_extra")``: the class must declare an attribute named
      ``_extra`` holding a mapping. The mapping is read from, and written back
      to, the reserved nested dictionary key ``_extra``; other unknown top-level
      keys still follow the converter's extra-keys policy.

    **Conflict rules**

    * A field declaration whose target attribute does not exist, or which carries
      a ``rename`` override, raises `ValueError` when the hook is generated.
    * In ``key`` mode, a reserved key holding a non-mapping value raises
      `TypeError` during structuring.
    * On unstructuring, a retained key colliding with a key produced by a
      declared field (for example because the field was renamed, or a removed
      field's old name was retained and later reused) raises `ValueError`; the
      declared value always wins, the conflict is never hidden.
    * Re-structuring replaces the retained mapping wholesale with the values of
      the new input; it is never merged with the previously retained values.

    ..  versionadded:: NEXT
    """

    field: str | None = None
    key: str | None = None

    def __attrs_post_init__(self) -> None:
        if (self.field is None) == (self.key is None):
            raise ValueError(
                "Exactly one of `field` and `key` must be given to Extras()."
            )

    @property
    def target(self) -> str:
        """Name of the attribute backing the retained mapping."""
        return self.field if self.field is not None else self.key  # type: ignore[return-value]


neutral = AttributeOverride()
already_generating = local()
