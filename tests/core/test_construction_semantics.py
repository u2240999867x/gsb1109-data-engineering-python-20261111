"""Regression tests for construction semantics.

Every scenario builds a dataclass that combines ``InitVar``, keyword-only
fields and ``frozen=True`` and compares ``dacite.from_dict`` against calling
the generated constructor directly with the same arguments, observing call
counts of default factories and ``__post_init__`` rather than only the
resulting value.

Known divergences from direct construction are marked with ``xfail(strict=True)``
together with the minimal reproducing input and the current behaviour instead
of weakening the dataclass (e.g. dropping ``frozen``) to make the test pass.
"""
from dataclasses import dataclass, InitVar, field

import pytest

from dacite import from_dict, MissingValueError, WrongTypeError
from tests.common import init_var_type_support, kw_only_support


@kw_only_support
@init_var_type_support
def test_frozen_model_matches_direct_construction_call_counts():
    factory_calls = []
    post_init_calls = []

    @dataclass(frozen=True)
    class X:
        a: InitVar[int]
        base: int = field(kw_only=True)
        derived: int = field(init=False)
        label: str = field(kw_only=True, default_factory=lambda: (factory_calls.append(1), "label")[1])

        def __post_init__(self, a: int) -> None:
            post_init_calls.append(a)
            object.__setattr__(self, "derived", a * 10)

    data = {"a": 3, "base": 7}

    direct = X(a=3, base=7)
    direct_factory_calls = len(factory_calls)
    direct_post_init_calls = list(post_init_calls)

    factory_calls.clear()
    post_init_calls.clear()
    result = from_dict(X, data)

    assert result == direct
    assert len(factory_calls) == direct_factory_calls == 1
    assert post_init_calls == direct_post_init_calls == [3]


@kw_only_support
def test_default_factory_is_not_called_when_value_is_supplied():
    factory_calls = []

    @dataclass(frozen=True)
    class X:
        i: int
        k: str = field(kw_only=True, default_factory=lambda: (factory_calls.append(1), "factory")[1])

    direct = X(i=1, k="given")
    direct_factory_calls = len(factory_calls)

    factory_calls.clear()
    result = from_dict(X, {"i": 1, "k": "given"})

    assert result == direct
    assert len(factory_calls) == direct_factory_calls == 0


@kw_only_support
@pytest.mark.xfail(
    strict=True,
    reason=(
        'minimal input {}: dacite eagerly calls default_factory in '
        'get_default_value_for_field while iterating fields, so the factory of '
        '"early" runs once before MissingValueError is raised for the required '
        'field "later_required"; direct construction fails while binding '
        'arguments and never calls the factory. frozen is kept intentionally.'
    ),
)
def test_default_factory_not_called_when_required_field_is_missing():
    factory_calls = []
    post_init_calls = []

    @dataclass(frozen=True)
    class X:
        early: int = field(kw_only=True, default_factory=lambda: (factory_calls.append(1), 1)[1])
        later_required: int

        def __post_init__(self) -> None:
            post_init_calls.append(1)

    with pytest.raises(TypeError):
        X()
    assert factory_calls == []
    assert post_init_calls == []

    with pytest.raises(MissingValueError):
        from_dict(X, {})

    assert factory_calls == []
    assert post_init_calls == []


@kw_only_support
@pytest.mark.xfail(
    strict=True,
    reason=(
        'minimal input {"later_required": "bad"}: dacite runs the '
        'default_factory of the earlier field "early" before the wrong type of '
        '"later_required" is reported, while direct construction never reaches '
        'the factory. frozen is kept intentionally.'
    ),
)
def test_default_factory_not_called_when_later_field_has_wrong_type():
    factory_calls = []

    @dataclass(frozen=True)
    class X:
        early: int = field(kw_only=True, default_factory=lambda: (factory_calls.append(1), 1)[1])
        later_required: int

    with pytest.raises(WrongTypeError):
        from_dict(X, {"later_required": "bad"})

    assert factory_calls == []


@init_var_type_support
def test_post_init_receives_init_var_value_exactly_once_like_direct_construction():
    post_init_calls = []

    @dataclass(frozen=True)
    class X:
        a: InitVar[int]
        k: int
        derived: int = field(init=False)

        def __post_init__(self, a: int) -> None:
            post_init_calls.append(a)
            object.__setattr__(self, "derived", a * 10)

    direct = X(a=4, k=1)
    direct_post_init_calls = list(post_init_calls)

    post_init_calls.clear()
    result = from_dict(X, {"a": 4, "k": 1})

    assert result == direct
    assert post_init_calls == direct_post_init_calls == [4]
    assert len(post_init_calls) == 1


@init_var_type_support
def test_post_init_receives_init_var_default_when_input_omitted_like_direct_construction():
    post_init_calls = []

    @dataclass(frozen=True)
    class X:
        a: InitVar[int] = 5
        out: int = field(init=False)

        def __post_init__(self, a: int) -> None:
            post_init_calls.append(a)
            object.__setattr__(self, "out", a)

    direct = X()
    direct_post_init_calls = list(post_init_calls)

    post_init_calls.clear()
    result = from_dict(X, {})

    assert result.out == direct.out == 5
    assert post_init_calls == direct_post_init_calls == [5]


@init_var_type_support
def test_frozen_non_init_field_without_default_is_left_unset_like_direct_construction():
    @dataclass(frozen=True)
    class X:
        a: InitVar[int]
        b: int = field(init=False)

        def __post_init__(self, a: int) -> None:
            object.__setattr__(self, "c", a)

    direct = X(a=1)
    result = from_dict(X, {"a": 1})

    assert not hasattr(result, "b")
    assert not hasattr(direct, "b")
    assert result.c == direct.c == 1


def test_frozen_non_init_field_silently_ignores_input_like_direct_construction():
    @dataclass(frozen=True)
    class X:
        a: int
        b: int = field(init=False, default=99)

    with pytest.raises(TypeError):
        X(a=1, b=2)

    result = from_dict(X, {"a": 1, "b": 2})

    assert result == X(a=1)
    assert result.b == 99


@pytest.mark.xfail(
    strict=True,
    reason=(
        'minimal input {"a": 1, "b": 2}: without frozen, dacite constructs the '
        'object with init fields only and then setattr()s the init=False field '
        '"b" to 2 (post_init_values in dacite.core.from_dict), while direct '
        'construction rejects "b" as an unexpected keyword argument and keeps '
        'the default 99; Config(strict=True) does not catch it because "b" is a '
        'declared field. The frozen sibling test documents the aligned behaviour.'
    ),
)
def test_non_init_field_input_is_rejected_like_direct_construction():
    @dataclass
    class X:
        a: int
        b: int = field(init=False, default=99)

    with pytest.raises(TypeError):
        X(a=1, b=2)

    result = from_dict(X, {"a": 1, "b": 2})

    assert result == X(a=1)
    assert result.b == 99


@init_var_type_support
def test_post_init_failure_propagates_after_a_single_construction_like_direct():
    post_init_calls = []

    class Boom(ValueError):
        pass

    @dataclass(frozen=True)
    class X:
        a: InitVar[int]

        def __post_init__(self, a: int) -> None:
            post_init_calls.append(a)
            raise Boom("boom")

    with pytest.raises(Boom):
        X(a=1)
    direct_post_init_calls = len(post_init_calls)

    post_init_calls.clear()
    with pytest.raises(Boom):
        from_dict(X, {"a": 1})

    assert len(post_init_calls) == direct_post_init_calls == 1


def test_failing_default_factory_propagates_without_constructing_like_direct():
    post_init_calls = []

    class FactoryBoom(RuntimeError):
        pass

    def broken_factory() -> str:
        raise FactoryBoom("factory down")

    @dataclass(frozen=True)
    class X:
        a: int
        k: str = field(default_factory=broken_factory)

        def __post_init__(self) -> None:
            post_init_calls.append(1)

    with pytest.raises(FactoryBoom):
        X(a=1)
    direct_post_init_calls = len(post_init_calls)

    post_init_calls.clear()
    with pytest.raises(FactoryBoom):
        from_dict(X, {"a": 1})

    assert len(post_init_calls) == direct_post_init_calls == 0


@kw_only_support
@init_var_type_support
def test_missing_required_keyword_only_field_fails_before_construction():
    post_init_calls = []

    @dataclass(frozen=True)
    class X:
        a: InitVar[int]
        k: int = field(kw_only=True)
        doubled: int = field(init=False)

        def __post_init__(self, a: int) -> None:
            post_init_calls.append(a)
            object.__setattr__(self, "doubled", a * 2)

    with pytest.raises(TypeError):
        X(a=1)

    with pytest.raises(MissingValueError):
        from_dict(X, {"a": 1})

    assert post_init_calls == []
