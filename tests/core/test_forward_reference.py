from dataclasses import dataclass
from typing import Generic, List, Optional, TypeVar

import pytest

from dacite import Config, ForwardReferenceError, from_dict
from tests.core.fixtures.import_plugins import schema
from tests.core.fixtures.import_plugins.alpha import models as alpha_plugin
from tests.core.fixtures.import_plugins.beta import models as beta_plugin

T = TypeVar("T")


@dataclass
class Person:
    name: str
    children: Optional[List["Person"]] = None


@dataclass
class Club:
    name: str
    members: List["Person"]


@dataclass
class Employee:
    name: str


@dataclass
class Team(Generic[T]):
    name: str
    members: Optional[List[T]] = None
    subteams: Optional[List["Team[T]"]] = None


def test_self_reference():
    data = {"name": "John Doe", "children": [{"name": "Jane Doe"}]}

    result = from_dict(Person, data)

    assert result == Person(name="John Doe", children=[Person(name="Jane Doe")])


def test_other_reference():
    data = {"name": "FooBar", "members": [{"name": "John Doe", "children": [{"name": "Jane Doe"}]}]}

    result = from_dict(Club, data)

    assert result == Club(name="FooBar", members=[Person(name="John Doe", children=[Person(name="Jane Doe")])])


def test_generic_self_reference():
    data = {"name": "foo", "members": [{"name": "John"}], "subteams": [{"name": "bar", "members": [{"name": "Jane"}]}]}

    result = from_dict(Team[Employee], data)

    assert result == Team(
        name="foo", members=[Employee(name="John")], subteams=[Team(name="bar", members=[Employee(name="Jane")])]
    )


def test_each_plugin_resolves_same_reference_name_within_its_own_context():
    data = {"nodes": [{"label": "root"}], "nested": {"nodes": [{"label": "child"}]}}

    alpha_result = from_dict(schema.Graph, data, Config(forward_references={"Node": alpha_plugin.Node}))
    beta_result = from_dict(schema.Graph, data, Config(forward_references={"Node": beta_plugin.Node}))

    assert type(alpha_result.nodes[0]) is alpha_plugin.Node
    assert type(beta_result.nodes[0]) is beta_plugin.Node
    assert type(alpha_result.nested.nodes[0]) is alpha_plugin.Node
    assert type(beta_result.nested.nodes[0]) is beta_plugin.Node


def test_updating_one_context_does_not_change_resolution_of_another_context():
    data = {"nodes": [{"label": "root"}]}

    alpha_config = Config(forward_references={"Node": alpha_plugin.Node})
    beta_config = Config(forward_references={"Node": beta_plugin.Node})

    assert type(from_dict(schema.Graph, data, alpha_config).nodes[0]) is alpha_plugin.Node
    assert type(from_dict(schema.Graph, data, beta_config).nodes[0]) is beta_plugin.Node
    assert type(from_dict(schema.Graph, data, alpha_config).nodes[0]) is alpha_plugin.Node


def test_missing_reference_is_reported_with_its_field_without_scanning_other_modules():
    # The ancestor package re-exports the beta plugin's Node, so a sys.modules scan
    # would silently pick the wrong record instead of failing.
    with pytest.raises(ForwardReferenceError) as exception_info:
        from_dict(schema.Graph, {"nodes": [{"label": "root"}]})

    assert str(exception_info.value) == 'can not resolve forward reference: "Node" in field "nodes"'
    assert exception_info._excinfo[1].__suppress_context__
