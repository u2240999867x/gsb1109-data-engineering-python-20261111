from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Graph:
    """Shared import schema.

    ``Node`` is deliberately *not* importable from this module: every import plugin
    supplies its own ``Node`` record through a local forward-reference context.
    """

    nodes: List["Node"] = field(default_factory=list)
    nested: Optional["Graph"] = None
