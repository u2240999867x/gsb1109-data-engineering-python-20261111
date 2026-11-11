from dataclasses import dataclass


@dataclass
class Node:
    plugin: str = "alpha"
    label: str = ""
