from dataclasses import dataclass


@dataclass(frozen=True)
class _Missing:
    """Sentinel marking a field that was not included in a patch payload."""

    def __repr__(self) -> str:
        return "MISSING"


MISSING = _Missing()
