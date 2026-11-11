from dataclasses import dataclass


@dataclass
class Node:
    plugin: str = "beta"
    label: str = ""


@dataclass
class BetaOnly:
    marker: str = "beta-only"
