from typing import Any

import pytest

from tomlkit import dumps
from tomlkit import parse
from tomlkit.exceptions import NonExistentKey
from tomlkit.exceptions import TOMLKitError


def _values(content: str) -> dict[str, Any]:
    return parse(content).unwrap()


def test_move_header_table_to_end_with_comments() -> None:
    content = """\
root = 1

# datasource comment
[servers.alpha]
ip = "10.0.0.1"

[servers.beta]
ip = "10.0.0.2"

[other]
x = 1
"""
    doc = parse(content)
    doc.move_to_end("servers")

    expected = """\
root = 1
[other]
x = 1

# datasource comment
[servers.alpha]
ip = "10.0.0.1"

[servers.beta]
ip = "10.0.0.2"

"""
    assert dumps(doc) == expected
    assert parse(dumps(doc)).unwrap() == _values(content)


def test_move_leaves_unselected_leading_separator_comment() -> None:
    content = """\
root = 1

# separator comment
[servers.alpha]
ip = "10.0.0.1"

[other]
x = 1
"""
    doc = parse(content)
    doc.move_to_end("servers", before=False)

    expected = """\
root = 1

# separator comment
[other]
x = 1
[servers.alpha]
ip = "10.0.0.1"

"""
    assert dumps(doc) == expected
    assert parse(dumps(doc)).unwrap() == _values(content)


def test_move_leaves_unselected_trailing_separator_whitespace() -> None:
    # The blank line between the dotted entry and the next header is a
    # document-level detached run; with ``after=False`` it stays in place
    # instead of following the moved table.
    content = """\
a.b.c = 1

[tail]
z = 9
"""
    doc = parse(content)
    doc.move_to_end("a", after=False)

    expected = """\

[tail]
z = 9

[a]
b.c = 1
"""
    assert dumps(doc) == expected
    assert parse(dumps(doc)).unwrap() == _values(content)


def test_move_implicit_parent_table_keeps_a_single_definition() -> None:
    # `servers` only exists implicitly through `[servers.alpha]` /
    # `[servers.beta]`; moving it must not recreate or duplicate the parent.
    content = """\
[servers.alpha]
ip = "10.0.0.1"

[servers.beta]
ip = "10.0.0.2"

[other]
x = 1
"""
    doc = parse(content)
    doc.move_to_end("servers")

    output = dumps(doc)
    assert output.count("[servers.alpha]") == 1
    assert output.count("[servers.beta]") == 1
    assert parse(output).unwrap() == _values(content)


def test_move_dotted_key_table_emits_a_single_header() -> None:
    content = """\
# lead comment
a.b.c = 1 # inline comment
# note between the fragments
a.b.d = 2

[tail]
z = 9
"""
    doc = parse(content)
    doc.move_to_end("a")

    expected = """\
[tail]
z = 9
# lead comment
# note between the fragments

[a.b]
c = 1 # inline comment
d = 2

"""
    assert dumps(doc) == expected
    reparsed = parse(dumps(doc))
    assert reparsed.unwrap() == _values(content)
    assert dumps(doc).count("[a.b]") == 1


def test_move_dotted_key_table_without_following_header_stays_inline() -> None:
    content = """\
a.b.c = 1
x = 9
"""
    doc = parse(content)
    doc.move_to_end("a")

    expected = """\
x = 9
a.b.c = 1
"""
    assert dumps(doc) == expected
    assert parse(dumps(doc)).unwrap() == _values(content)


def test_move_diverging_dotted_paths() -> None:
    content = """\
a.b.c = 1
a.e.f = 2

[tail]
z = 9
"""
    doc = parse(content)
    doc.move_to_end("a")

    output = dumps(doc)
    assert parse(output).unwrap() == _values(content)
    assert "[a.b]" in output
    assert "[a.e]" in output


def test_move_array_of_tables_keeps_all_elements_together() -> None:
    content = """\
root = 1

# products comment
[[products]]
name = "first"

[[products]]
name = "second"

[after]
q = 1
"""
    doc = parse(content)
    doc.move_to_end("products")

    expected = """\
root = 1
[after]
q = 1

# products comment
[[products]]
name = "first"

[[products]]
name = "second"

"""
    assert dumps(doc) == expected
    assert dumps(doc).count("[[products]]") == 2
    assert parse(dumps(doc)).unwrap() == _values(content)


def test_move_array_of_tables_with_nested_subtable() -> None:
    content = """\
[[products]]
name = "first"

[[products]]
name = "second"

[products.flag]
enabled = true

[tail]
z = 9
"""
    doc = parse(content)
    doc.move_to_end("products")

    output = dumps(doc)
    assert parse(output).unwrap() == _values(content)
    assert output.index("[[products]]") > output.index("[tail]")


def test_move_out_of_order_table_parts_together() -> None:
    content = """\
[a.b]
x = 1

[c]
w = 4

[a]
y = 2

[d]
z = 3
"""
    doc = parse(content)
    doc.move_to_end("a")

    output = dumps(doc)
    assert parse(output).unwrap() == _values(content)
    # Both fragments of `a` end up after every table that stays in place.
    assert output.index("[a.b]") > output.index("[d]")
    assert output.index("[a]\n") > output.index("[d]")


def test_move_group_of_tables_preserves_relative_order() -> None:
    content = """\
# ds1 comment
[ds1]
a = 1

# ds2 comment
[ds2]
b = 2

[keep]
c = 3
"""
    doc = parse(content)
    doc.move_to_end(["ds1", "ds2"])

    expected = """\
[keep]
c = 3
# ds1 comment
[ds1]
a = 1

# ds2 comment
[ds2]
b = 2

"""
    assert dumps(doc) == expected
    assert list(doc.keys()) == ["keep", "ds1", "ds2"]
    assert parse(dumps(doc)).unwrap() == _values(content)


def test_move_nested_table_through_table_method() -> None:
    content = """\
[sec]
keep = 1

# ds one
[sec.one]
v = 1

[sec.two]
v = 2
"""
    doc = parse(content)
    doc["sec"].move_to_end("one")

    expected = """\
[sec]
keep = 1
[sec.two]
v = 2

# ds one
[sec.one]
v = 1

"""
    assert dumps(doc) == expected
    assert parse(dumps(doc)).unwrap() == _values(content)


def test_move_already_last_table_is_a_noop() -> None:
    content = """\
[a]
x = 1

[b]
y = 2
"""
    doc = parse(content)
    doc.move_to_end("b")

    assert dumps(doc) == content


def test_move_is_idempotent() -> None:
    content = """\
[one]
a = 1

[two]
b = 2

[three]
c = 3
"""
    doc = parse(content)
    doc.move_to_end("one")
    once = dumps(doc)
    doc.move_to_end("one")

    assert dumps(doc) == once
    assert parse(dumps(doc)).unwrap() == _values(content)


def test_move_rejects_missing_key() -> None:
    doc = parse("a = 1\n[b]\nx = 2\n")
    with pytest.raises(NonExistentKey):
        doc.move_to_end("missing")


def test_move_rejects_non_table_value() -> None:
    doc = parse("a = 1\n[b]\nx = 2\n")
    with pytest.raises(TOMLKitError):
        doc.move_to_end("a")


def test_moved_table_can_still_be_edited() -> None:
    content = """\
[x]
a = 1

[y]
b = 2
"""
    doc = parse(content)
    doc.move_to_end("x")
    doc["x"]["a"] = 42
    doc["z"] = {"k": 9}

    reparsed = parse(dumps(doc))
    assert reparsed["x"]["a"] == 42
    assert reparsed["z"]["k"] == 9
    # New entries are appended after the relocated table; the relative order of
    # the existing tables (``y`` before the moved ``x``) is unchanged.
    assert list(reparsed.keys()) == ["y", "x", "z"]


def test_move_survives_deepcopy_and_pickle() -> None:
    import copy
    import pickle

    content = """\
# lead
[x]
a = 1

[y]
b = 2
"""
    doc = parse(content)
    doc.move_to_end("x")
    serialized = dumps(doc)

    for clone in (copy.deepcopy(doc), pickle.loads(pickle.dumps(doc))):
        assert dumps(clone) == serialized
        assert clone.unwrap() == doc.unwrap()
