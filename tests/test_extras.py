"""Tests for the `Extras` extra-field retention convention."""

from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any, Generic, TypeVar

import pytest
from attrs import Factory, define, field, frozen

from cattrs import BaseConverter, Converter
from cattrs.errors import ClassValidationError, ForbiddenExtraKeysError
from cattrs.gen import (
    Extras,
    make_dict_structure_fn,
    make_dict_unstructure_fn,
    override,
)


@define
class FieldMode:
    name: str
    extra: dict[str, Any] = Factory(dict)

    __cattrs_extras__ = Extras(field="extra")


@define
class KeyMode:
    name: str
    _extra: dict[str, Any] = Factory(dict)

    __cattrs_extras__ = Extras(key="_extra")


def test_field_mode_roundtrip(converter):
    """Unknown keys are retained on the declared attribute and restored."""
    raw = {"name": "n", "legacy_flag": True, "old_count": 3}

    inst = converter.structure(raw, FieldMode)

    assert inst == FieldMode("n", {"legacy_flag": True, "old_count": 3})
    assert converter.unstructure(inst) == raw


def test_field_mode_no_extras(converter):
    """Nothing is retained when there are no unknown keys."""
    inst = converter.structure({"name": "n"}, FieldMode)

    assert inst.extra == {}
    assert converter.unstructure(inst) == {"name": "n"}


def test_field_mode_known_keys_not_retained(converter):
    """Declared field keys never end up in the retention attribute."""
    inst = converter.structure({"name": "n", "extra": {"a": 1}}, FieldMode)

    # The reserved name itself is treated as a seed mapping.
    assert inst.extra == {"a": 1}
    assert converter.unstructure(inst) == {"name": "n", "a": 1}


def test_field_mode_seed_merges_with_unknown_keys(converter):
    """A payload using the reserved name seeds the retention mapping."""
    inst = converter.structure(
        {"name": "n", "extra": {"seeded": 1}, "other": 2}, FieldMode
    )

    assert inst.extra == {"seeded": 1, "other": 2}


def test_field_mode_seed_not_a_mapping(converter):
    """A non-mapping value under the reserved name is a clear error."""
    with pytest.raises(TypeError, match="must contain a mapping"):
        converter.structure({"name": "n", "extra": 42}, FieldMode)


def test_field_mode_unstructure_collision(converter):
    """Retained keys colliding with declared field keys raise on unstructure."""
    inst = FieldMode("n", {"name": "stale"})

    with pytest.raises(ValueError, match="collide with declared field"):
        converter.unstructure(inst)


def test_field_mode_restructure_replaces(converter):
    """Re-structuring replaces the retained mapping; it is never merged."""
    inst = converter.structure({"name": "n", "a": 1}, FieldMode)
    again = converter.structure(converter.unstructure(inst), FieldMode)

    assert again.extra == {"a": 1}

    # A newer payload without the old key drops it.
    newer = converter.structure({"name": "n", "b": 2}, FieldMode)
    assert newer.extra == {"b": 2}


def test_key_mode_roundtrip(converter):
    """The reserved nested key is retained; other unknown keys are not."""
    raw = {"name": "n", "_extra": {"a": 1}, "dropped": True}

    inst = converter.structure(raw, KeyMode)

    assert inst._extra == {"a": 1}
    assert converter.unstructure(inst) == {"name": "n", "_extra": {"a": 1}}


def test_key_mode_missing_reserved_key(converter):
    """A missing reserved key falls back to the attribute default."""
    inst = converter.structure({"name": "n"}, KeyMode)

    assert inst._extra == {}


def test_key_mode_reserved_key_not_a_mapping(converter):
    """A non-mapping reserved key value is a clear error."""
    with pytest.raises(TypeError, match="must contain a mapping"):
        converter.structure({"name": "n", "_extra": 42}, KeyMode)


def test_key_mode_forbids_other_unknown_keys():
    """In key mode, other unknown keys still follow the converter policy."""
    converter = Converter(forbid_extra_keys=True)

    with pytest.raises((ForbiddenExtraKeysError, ClassValidationError)):
        converter.structure({"name": "n", "junk": 1}, KeyMode)

    inst = converter.structure({"name": "n", "_extra": {"a": 1}}, KeyMode)
    assert inst._extra == {"a": 1}


def test_undeclared_class_default_policy(converter):
    """Classes without a declaration keep the original extra-keys policy."""

    @define
    class Plain:
        name: str

    inst = converter.structure({"name": "n", "unknown": 1}, Plain)

    assert not hasattr(inst, "unknown")
    assert converter.unstructure(inst) == {"name": "n"}


def test_undeclared_class_forbid_policy():
    """`forbid_extra_keys` still applies to classes without a declaration."""

    @define
    class Plain:
        name: str

    converter = Converter(forbid_extra_keys=True)

    with pytest.raises((ForbiddenExtraKeysError, ClassValidationError)):
        converter.structure({"name": "n", "unknown": 1}, Plain)


def test_field_mode_with_forbid_extra_keys():
    """Retention takes precedence over `forbid_extra_keys` for declared classes."""
    converter = Converter(forbid_extra_keys=True)

    inst = converter.structure({"name": "n", "unknown": 1}, FieldMode)

    assert inst.extra == {"unknown": 1}
    assert converter.unstructure(inst) == {"name": "n", "unknown": 1}


def test_renamed_field(converter):
    """A renamed field's new key is known; its old name is retained."""

    @define
    class Renamed:
        name: str
        extra: dict[str, Any] = Factory(dict)

        __cattrs_extras__ = Extras(field="extra")

    if isinstance(converter, BaseConverter) and not isinstance(converter, Converter):
        pytest.skip("BaseConverter does not support per-field renames")

    converter.register_structure_hook(
        Renamed,
        make_dict_structure_fn(Renamed, converter, name=override(rename="fullName")),
    )
    converter.register_unstructure_hook(
        Renamed,
        make_dict_unstructure_fn(Renamed, converter, name=override(rename="fullName")),
    )

    inst = converter.structure({"fullName": "n", "name": "old"}, Renamed)

    assert inst.name == "n"
    assert inst.extra == {"name": "old"}
    assert converter.unstructure(inst) == {"fullName": "n", "name": "old"}


def test_stale_retained_key_of_renamed_field(converter):
    """A retained old name colliding with a rename target raises."""

    @define
    class Renamed:
        name: str
        extra: dict[str, Any] = Factory(dict)

        __cattrs_extras__ = Extras(field="extra")

    if isinstance(converter, BaseConverter) and not isinstance(converter, Converter):
        pytest.skip("BaseConverter does not support per-field renames")

    converter.register_unstructure_hook(
        Renamed,
        make_dict_unstructure_fn(Renamed, converter, name=override(rename="fullName")),
    )

    inst = Renamed("n", {"fullName": "stale"})

    with pytest.raises(ValueError, match="collide with declared field"):
        converter.unstructure(inst)


def test_extras_opt_out():
    """`_cattrs_extras=None` ignores the class-level declaration."""
    converter = Converter()

    hook = make_dict_structure_fn(FieldMode, converter, _cattrs_extras=None)
    inst = hook({"name": "n", "unknown": 1}, FieldMode)

    assert inst.extra == {}

    unhook = make_dict_unstructure_fn(FieldMode, converter, _cattrs_extras=None)
    assert unhook(FieldMode("n", {"a": 1})) == {"name": "n", "extra": {"a": 1}}


def test_extras_explicit_override():
    """An explicit `_cattrs_extras` wins over the class-level declaration."""

    @define
    class Plain:
        name: str
        extra: dict[str, Any] = Factory(dict)

    converter = Converter()

    hook = make_dict_structure_fn(
        Plain, converter, _cattrs_extras=Extras(field="extra")
    )
    inst = hook({"name": "n", "unknown": 1}, Plain)

    assert inst.extra == {"unknown": 1}


def test_extras_target_must_exist():
    """The declared target must be an attribute of the class."""

    @define
    class Missing:
        name: str

        __cattrs_extras__ = Extras(field="nope")

    with pytest.raises(ValueError, match="not an attribute"):
        make_dict_structure_fn(Missing, Converter())


def test_extras_target_needs_default():
    """The field-mode target must have a default or be init=False."""

    @define
    class NoDefault:
        name: str
        extra: dict[str, Any]

        __cattrs_extras__ = Extras(field="extra")

    with pytest.raises(ValueError, match="must have a default"):
        make_dict_structure_fn(NoDefault, Converter())


def test_extras_target_init_false():
    """An init=False target works."""

    @define
    class InitFalse:
        name: str
        extra: dict[str, Any] = field(init=False, default=Factory(dict))

        __cattrs_extras__ = Extras(field="extra")

    converter = Converter()
    inst = converter.structure({"name": "n", "u": 1}, InitFalse)

    assert inst.extra == {"u": 1}
    assert converter.unstructure(inst) == {"name": "n", "u": 1}


def test_extras_target_cannot_be_renamed():
    """A rename override on the target fails fast at hook generation."""
    converter = Converter()

    with pytest.raises(ValueError, match="cannot be renamed"):
        make_dict_structure_fn(FieldMode, converter, extra=override(rename="extras"))

    with pytest.raises(ValueError, match="cannot be renamed"):
        make_dict_unstructure_fn(FieldMode, converter, extra=override(rename="extras"))


def test_extras_frozen_class(converter):
    """Frozen classes get a clear error instead of a failed setattr."""

    @frozen
    class Frozen:
        name: str
        extra: dict[str, Any] = Factory(dict)

        __cattrs_extras__ = Extras(field="extra")

    with pytest.raises(AttributeError, match="frozen or read-only"):
        converter.structure({"name": "n", "u": 1}, Frozen)


def test_extras_dataclass(converter):
    """Dataclasses support the convention too."""

    @dataclass
    class DC:
        name: str
        extra: dict = dc_field(default_factory=dict)

        __cattrs_extras__ = Extras(field="extra")

    inst = converter.structure({"name": "n", "u": 1}, DC)

    assert inst.extra == {"u": 1}
    assert converter.unstructure(inst) == {"name": "n", "u": 1}


def test_extras_use_alias():
    """Aliases are known keys when `use_alias` is enabled."""

    @define
    class Aliased:
        name: str = field(alias="fullName")
        extra: dict[str, Any] = Factory(dict)

        __cattrs_extras__ = Extras(field="extra")

    converter = Converter(use_alias=True)
    inst = converter.structure({"fullName": "n", "u": 1}, Aliased)

    assert inst.extra == {"u": 1}
    assert converter.unstructure(inst) == {"fullName": "n", "u": 1}


def test_extras_omit_if_default():
    """Retention composes with `omit_if_default`."""
    converter = Converter(omit_if_default=True)

    inst = converter.structure({"name": "n", "u": 1}, FieldMode)

    assert converter.unstructure(inst) == {"name": "n", "u": 1}
    assert converter.unstructure(FieldMode("n")) == {"name": "n"}


def test_extras_generic_class():
    """Generic classes retain extras through their resolved hooks."""
    T = TypeVar("T")

    @define
    class GenericRec(Generic[T]):
        val: T
        extra: dict[str, Any] = Factory(dict)

        __cattrs_extras__ = Extras(field="extra")

    converter = Converter()
    inst = converter.structure({"val": "1", "u": 1}, GenericRec[int])

    assert inst.extra == {"u": 1}
    assert converter.unstructure(inst) == {"val": 1, "u": 1}


def test_extras_nested_classes(converter):
    """Retention applies independently at every nesting level."""

    @define
    class Inner:
        a: int
        extra: dict[str, Any] = Factory(dict)

        __cattrs_extras__ = Extras(field="extra")

    @define
    class Outer:
        inner: Inner
        extra: dict[str, Any] = Factory(dict)

        __cattrs_extras__ = Extras(field="extra")

    raw = {"inner": {"a": 1, "inner_unknown": "x"}, "outer_unknown": 2}
    inst = converter.structure(raw, Outer)

    assert inst.inner.extra == {"inner_unknown": "x"}
    assert inst.extra == {"outer_unknown": 2}
    assert converter.unstructure(inst) == raw


def test_extras_declaration_must_be_extras_instance():
    """Garbage in `__cattrs_extras__` is a clear error."""

    @define
    class Bad:
        name: str
        extra: dict[str, Any] = Factory(dict)

        __cattrs_extras__ = "extra"

    with pytest.raises(TypeError, match="must be an Extras instance"):
        make_dict_structure_fn(Bad, Converter())


def test_extras_constructor_validation():
    """Exactly one of `field` and `key` is required."""
    with pytest.raises(ValueError, match="Exactly one"):
        Extras()

    with pytest.raises(ValueError, match="Exactly one"):
        Extras(field="a", key="b")


def test_extras_unstructure_target_not_a_mapping(converter):
    """A non-mapping retention attribute is a clear error on unstructure."""
    inst = FieldMode("n", {"a": 1})
    object.__setattr__(inst, "extra", 42)

    with pytest.raises(TypeError, match="must hold a mapping"):
        converter.unstructure(inst)
