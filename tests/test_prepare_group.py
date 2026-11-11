"""Tests for `BaseConverter.prepare_group` and `PreparedGroup`."""

from __future__ import annotations

from threading import Thread
from time import sleep

import pytest
from attrs import define

from cattrs import BaseConverter, Converter, PreparedGroup


@define
class Ping:
    pong: Pong | None = None


@define
class Pong:
    ping: Ping | None = None


@define
class TreeNode:
    name: str
    child: TreeNode | None = None


@define
class Nested:
    inner: list[Nested]


def test_mutually_referencing_group():
    """A group of mutually-referencing types can be prepared and published."""
    converter = Converter()

    group = converter.prepare_group(Ping, Pong)

    assert isinstance(group, PreparedGroup)
    assert set(group.structure_hooks) == {Ping, Pong}
    assert set(group.unstructure_hooks) == {Ping, Pong}

    group.publish()

    orig = Ping(Pong(Ping()))
    unstructured = converter.unstructure(orig)
    assert unstructured == {"pong": {"ping": {"pong": None}}}
    assert converter.structure(unstructured, Ping) == orig


def test_self_referential_group():
    """Self-referencing types can be prepared and published."""
    converter = Converter()
    converter.prepare_group(TreeNode, Nested).publish()

    orig = TreeNode("a", TreeNode("b"))
    unstructured = converter.unstructure(orig)
    assert unstructured == {"name": "a", "child": {"name": "b", "child": None}}
    assert converter.structure(unstructured, TreeNode) == orig

    orig = Nested([Nested([])])
    unstructured = converter.unstructure(orig)
    assert unstructured == {"inner": [{"inner": []}]}
    assert converter.structure(unstructured, Nested) == orig


def test_base_converter_group():
    """Groups work on `BaseConverter` too."""
    converter = BaseConverter()
    converter.prepare_group(Ping, Pong).publish()

    assert converter.structure({"pong": {"ping": None}}, Ping) == Ping(Pong())
    assert converter.unstructure(Ping(Pong())) == {"pong": {"ping": None}}


def test_prepare_does_not_publish_until_publish_called():
    """Prepared hooks are not used by normal conversion before publishing."""
    converter = Converter()

    group = converter.prepare_group(Ping, Pong)
    prepared = group.structure_hooks[Ping]

    # Normal conversion lazily generates its own hooks until publish.
    assert converter.get_structure_hook(Ping) is not prepared

    group.publish()

    assert converter.get_structure_hook(Ping) is prepared
    assert converter.get_unstructure_hook(Pong) is group.unstructure_hooks[Pong]


def test_failed_prepare_leaves_no_residue_and_can_be_retried():
    """A failing factory aborts the whole group, leaving nothing behind."""
    converter = Converter()

    def bad_factory(type):
        raise RuntimeError("boom")

    converter.register_structure_hook_factory(lambda t: t is Pong, bad_factory)

    with pytest.raises(RuntimeError):
        converter.prepare_group(Ping, Pong)

    # No half-generated hooks remain published.
    assert Ping not in converter._structure_func._direct_dispatch
    assert Pong not in converter._structure_func._direct_dispatch
    assert Ping not in converter._unstructure_func._direct_dispatch
    assert Pong not in converter._unstructure_func._direct_dispatch

    # Normal lazy generation is unaffected by the failed preparation.
    assert converter.unstructure(Ping()) == {"pong": None}

    # The rules can be fixed and the preparation retried.
    converter.register_structure_hook_factory(
        lambda t: t is Pong, lambda t: lambda v, _: Pong(**v)
    )
    converter.prepare_group(Ping, Pong).publish()

    assert converter.structure({"pong": {"ping": None}}, Ping) == Ping(Pong())


def test_concurrent_conversion_during_prepare():
    """Other threads never see unfinished hooks while a group is prepared."""
    converter = Converter()
    stop = False
    errors = []

    def slow_bad_factory(type):
        sleep(0.001)
        raise RuntimeError("boom")

    def worker():
        try:
            while not stop:
                assert converter.structure(
                    {"name": "a", "child": {"name": "b", "child": None}}, TreeNode
                ) == TreeNode("a", TreeNode("b"))
                assert converter.unstructure(TreeNode("a", TreeNode("b"))) == {
                    "name": "a",
                    "child": {"name": "b", "child": None},
                }
        except Exception as exc:
            errors.append(exc)

    converter.register_structure_hook_factory(lambda t: t is Pong, slow_bad_factory)

    thread = Thread(target=worker)
    thread.start()
    try:
        for _ in range(20):
            with pytest.raises(RuntimeError):
                converter.prepare_group(Ping, Pong)
    finally:
        stop = True
        thread.join()

    assert not errors
