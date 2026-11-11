import sys
from dataclasses import dataclass, field
from typing import Dict, Any, Callable, Optional, Type, List

from dacite.frozen_dict import FrozenDict

if sys.version_info >= (3, 8):
    from functools import cached_property  # type: ignore  # pylint: disable=no-name-in-module
else:
    # Remove when we drop support for Python<3.8
    cached_property = property  # type: ignore  # pylint: disable=invalid-name


# A metadata hook receives the input value together with the marker found in a
# typing.Annotated alias and returns the value after applying the marker's rule.
MetadataHook = Callable[[Any, Any], Any]


@dataclass
class Config:
    type_hooks: Dict[Type, Callable[[Any], Any]] = field(default_factory=dict)
    cast: List[Type] = field(default_factory=list)
    forward_references: Optional[Dict[str, Any]] = None
    check_types: bool = True
    strict: bool = False
    strict_unions_match: bool = False
    convert_key: Callable[[str], str] = field(default_factory=lambda: lambda x: x)
    metadata_hooks: Dict[Any, MetadataHook] = field(default_factory=dict)
    reject_unregistered_metadata: bool = False

    @cached_property
    def hashable_forward_references(self) -> Optional[FrozenDict]:
        return FrozenDict(self.forward_references) if self.forward_references else None
