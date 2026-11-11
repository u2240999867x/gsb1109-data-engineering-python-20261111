import pytest

import tomlkit

from tomlkit import dumps
from tomlkit import parse


def test_sort_keys_orders_entries_and_attaches_comments_to_next_item() -> None:
    doc = parse(
        """\
[sources]
# primary warehouse
warehouse = "pg"

cache = "redis"
# legacy, drop after migration
legacy = "mysql"
"""
    )

    doc["sources"].sort_keys()

    assert (
        dumps(doc)
        == """\
[sources]

cache = "redis"
# legacy, drop after migration
legacy = "mysql"
# primary warehouse
warehouse = "pg"
"""
    )


def test_sort_keys_can_attach_comments_to_previous_item() -> None:
    doc = parse(
        """\
[sources]
warehouse = "pg"
# belongs to warehouse above

cache = "redis"
legacy = "mysql"
"""
    )

    doc["sources"].sort_keys(comments="previous")

    assert (
        dumps(doc)
        == """\
[sources]
cache = "redis"
legacy = "mysql"
warehouse = "pg"
# belongs to warehouse above

"""
    )


def test_sort_keys_treats_blank_line_separated_blocks_as_units() -> None:
    doc = parse(
        """\
[sources]
zebra = "z"

# keep these together
apple = "a"
avocado = "v"

mango = "m"
"""
    )

    doc["sources"].sort_keys(blocks=True)

    assert (
        dumps(doc)
        == """\
[sources]
# keep these together
apple = "a"
avocado = "v"

mango = "m"

zebra = "z"
"""
    )


def test_sort_keys_keeps_dotted_keys_dotted() -> None:
    doc = parse(
        """\
[sources]
zebra = 1
alpha.beta = 2
alpha.gamma = 3
mid = 4
"""
    )

    doc["sources"].sort_keys()

    assert (
        dumps(doc)
        == """\
[sources]
alpha.beta = 2
alpha.gamma = 3
mid = 4
zebra = 1
"""
    )
    # the dotted key still resolves to a nested mapping
    assert doc["sources"]["alpha"] == {"beta": 2, "gamma": 3}


def test_sort_keys_does_not_move_subtables_and_leaves_rest_of_file_untouched() -> None:
    before = """\
# file header
[meta]
id = 1

[sources]
zebra = 1
apple = 2

[sources.extra]
x = 1

[other]
y = 2
"""
    doc = parse(before)

    doc["sources"].sort_keys()

    after = dumps(doc)
    assert (
        after
        == """\
# file header
[meta]
id = 1

[sources]
apple = 2
zebra = 1

[sources.extra]
x = 1

[other]
y = 2
"""
    )
    # everything from the first sub-table header on is byte-for-byte identical
    assert (
        after[after.index("[sources.extra]") :]
        == before[before.index("[sources.extra]") :]
    )


def test_sort_keys_stops_before_arrays_of_tables() -> None:
    doc = parse(
        """\
[sources]
zebra = 1
apple = 2

[[sources.replicas]]
host = "a"
"""
    )

    doc["sources"].sort_keys()

    assert (
        dumps(doc)
        == """\
[sources]
apple = 2
zebra = 1

[[sources.replicas]]
host = "a"
"""
    )


def test_sort_keys_on_document_sorts_root_level_entries_only() -> None:
    doc = parse(
        """\
zebra = 1
apple = 2

[table]
b = 1
a = 2
"""
    )

    doc.sort_keys()

    assert (
        dumps(doc)
        == """\
apple = 2
zebra = 1

[table]
b = 1
a = 2
"""
    )


def test_sort_keys_supports_custom_key_and_reverse() -> None:
    doc = parse(
        """\
[t]
bb = 1
a = 2
ccc = 3
"""
    )

    doc["t"].sort_keys(key=len)
    assert list(doc["t"]) == ["a", "bb", "ccc"]

    doc["t"].sort_keys(reverse=True)
    assert (
        dumps(doc)
        == """\
[t]
ccc = 3
bb = 1
a = 2
"""
    )


def test_sort_keys_rejects_unknown_comment_policy() -> None:
    doc = parse("[t]\nb = 1\na = 2\n")

    with pytest.raises(ValueError):
        doc["t"].sort_keys(comments="sideways")


def test_sort_keys_round_trips_to_the_same_data() -> None:
    doc = parse(
        """\
[sources]
# primary warehouse
warehouse = "pg"
cache = "redis"
alpha.beta = 2

[sources.extra]
x = 1
"""
    )
    before = doc.unwrap()

    doc["sources"].sort_keys()

    assert parse(dumps(doc)).unwrap() == before


def test_sort_keys_preserves_missing_trailing_newline() -> None:
    doc = parse("[t]\nb = 2\na = 1")

    doc["t"].sort_keys()

    assert dumps(doc) == "[t]\na = 1\nb = 2"


def test_sort_keys_keeps_trailing_comments_at_the_end_of_the_table() -> None:
    doc = parse(
        """\
[t]
b = 2
a = 1
# closing note

[other]
x = 1
"""
    )

    doc["t"].sort_keys()

    assert (
        dumps(doc)
        == """\
[t]
a = 1
b = 2
# closing note

[other]
x = 1
"""
    )


def test_sort_keys_glues_comment_only_blocks_by_policy() -> None:
    doc = parse(
        """\
[t]
z = 1

# free floating

a = 2
"""
    )

    doc["t"].sort_keys(blocks=True)
    assert (
        dumps(doc)
        == """\
[t]
# free floating

a = 2

z = 1
"""
    )

    doc = parse(
        """\
[t]
z = 1

# free floating

a = 2
"""
    )
    doc["t"].sort_keys(blocks=True, comments="previous")
    assert (
        dumps(doc)
        == """\
[t]
a = 2

z = 1

# free floating
"""
    )


def test_sort_keys_is_a_no_op_when_already_sorted() -> None:
    src = "[t]\na = 1\nb = 2\n"
    doc = parse(src)

    doc["t"].sort_keys()

    assert dumps(doc) == src


def test_sort_keys_does_not_support_inline_tables() -> None:
    doc = parse("[t]\na = {y = 2, x = 1}\n")

    doc["t"]["a"].value.sort_keys()

    assert dumps(doc) == "[t]\na = {y = 2, x = 1}\n"


def test_sort_keys_returns_the_table_for_chaining() -> None:
    doc = parse("[t]\nb = 1\na = 2\n")
    table = doc["t"]

    assert table.sort_keys() is table
    assert isinstance(tomlkit.document().sort_keys(), tomlkit.TOMLDocument)
