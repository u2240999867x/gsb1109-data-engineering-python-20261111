from __future__ import annotations

import pytest

import tomlkit

from tomlkit import table_to_aot
from tomlkit.container import OutOfOrderTableProxy
from tomlkit.exceptions import MigrationError
from tomlkit.exceptions import TOMLKitError
from tomlkit.items import AoT
from tomlkit.items import SingleKey


def test_simple_table_becomes_a_single_element_aot() -> None:
    doc = tomlkit.parse('[datasource]\nurl = "pg"\n')

    result = table_to_aot(doc, "datasource")

    assert isinstance(result, AoT)
    assert isinstance(doc.item("datasource"), AoT)
    assert len(result) == 1
    assert result[0].is_aot_element()
    assert tomlkit.dumps(doc) == '[[datasource]]\nurl = "pg"\n'


def test_nested_subtable_stays_attached_to_the_source() -> None:
    source = (
        '[datasource]\nurl = "pg"\n\n[datasource.pool]\nmax = 10\n\n[other]\nz = 1\n'
    )
    doc = tomlkit.parse(source)

    table_to_aot(doc, "datasource")

    expected = (
        '[[datasource]]\nurl = "pg"\n\n[datasource.pool]\nmax = 10\n\n[other]\nz = 1\n'
    )
    assert tomlkit.dumps(doc) == expected
    migrated = tomlkit.parse(tomlkit.dumps(doc))
    assert migrated.unwrap() == {
        "datasource": [{"url": "pg", "pool": {"max": 10}}],
        "other": {"z": 1},
    }


def test_later_separate_subtable_keeps_its_original_owner() -> None:
    source = (
        '[datasource]\nurl = "pg"\n\n'
        "[other]\nz = 1\n\n"
        "# pool comment\n"
        "[datasource.pool]\nmax = 10\n\n"
        "[tail]\nw = 2\n"
    )
    doc = tomlkit.parse(source)

    table_to_aot(doc, "datasource")

    expected = (
        "[[datasource]]\n"
        'url = "pg"\n\n'
        "[datasource.pool]\nmax = 10\n\n"
        "[other]\nz = 1\n\n"
        "# pool comment\n"
        "[tail]\nw = 2\n"
    )
    assert tomlkit.dumps(doc) == expected
    # The sub-table must not be swept into an unrelated element: there is still
    # exactly one element and it owns the pool sub-table.
    element = doc.item("datasource")[0]
    assert element["pool"] == {"max": 10}
    assert doc.unwrap()["other"] == {"z": 1}
    assert doc.unwrap()["tail"] == {"w": 2}


def test_values_inline_comments_and_unrelated_tables_are_preserved() -> None:
    source = (
        'title = "demo"\n\n'
        "# the only data source\n"
        "[datasource]\n"
        'url = "postgres://localhost"\n'
        "pool_size = 10  # inline comment\n\n"
        "[datasource.tls]\nenabled = true\n\n"
        '[other]\nname = "kept"\n\n'
        "# pool belongs to datasource\n"
        "[datasource.pool]\nmax = 20\n\n"
        "[tail]\nz = 1\n"
    )
    doc = tomlkit.parse(source)

    table_to_aot(doc, "datasource")

    expected = (
        'title = "demo"\n\n'
        "# the only data source\n"
        "[[datasource]]\n"
        'url = "postgres://localhost"\n'
        "pool_size = 10  # inline comment\n\n"
        "[datasource.tls]\nenabled = true\n\n"
        "[datasource.pool]\nmax = 20\n\n"
        '[other]\nname = "kept"\n\n'
        "# pool belongs to datasource\n"
        "[tail]\nz = 1\n"
    )
    assert tomlkit.dumps(doc) == expected
    # Every value survives, merely nested one level deeper into the element.
    assert tomlkit.parse(tomlkit.dumps(doc)).unwrap() == {
        "title": "demo",
        "datasource": [
            {
                "url": "postgres://localhost",
                "pool_size": 10,
                "tls": {"enabled": True},
                "pool": {"max": 20},
            }
        ],
        "other": {"name": "kept"},
        "tail": {"z": 1},
    }


def test_dotted_key_origin_is_migrated() -> None:
    source = 'datasource.url = "pg"\ndatasource.pool.max = 10\n\n[other]\nz = 1\n'
    doc = tomlkit.parse(source)

    table_to_aot(doc, "datasource")

    expected = '[[datasource]]\nurl = "pg"\npool.max = 10\n\n[other]\nz = 1\n'
    assert tomlkit.dumps(doc) == expected


def test_implicit_super_table_origin_is_migrated() -> None:
    source = "[datasource.pool]\nmax = 10\n\n[other]\nz = 1\n"
    doc = tomlkit.parse(source)

    table_to_aot(doc, "datasource")

    expected = "[[datasource]]\n[datasource.pool]\nmax = 10\n\n[other]\nz = 1\n"
    assert tomlkit.dumps(doc) == expected


def test_nested_array_of_tables_inside_is_preserved() -> None:
    source = (
        '[datasource]\nurl = "pg"\n'
        '[[datasource.replicas]]\nhost = "a"\n'
        '[[datasource.replicas]]\nhost = "b"\n\n'
        "[other]\nz = 1\n"
    )
    doc = tomlkit.parse(source)

    table_to_aot(doc, "datasource")

    expected = (
        "[[datasource]]\n"
        'url = "pg"\n'
        '[[datasource.replicas]]\nhost = "a"\n'
        '[[datasource.replicas]]\nhost = "b"\n\n'
        "[other]\nz = 1\n"
    )
    assert tomlkit.dumps(doc) == expected


def test_split_nested_aot_fragments_are_merged_into_the_element() -> None:
    source = (
        '[datasource]\nurl = "pg"\n'
        '[[datasource.replicas]]\nhost = "a"\n\n'
        "[other]\nz = 1\n\n"
        '[[datasource.replicas]]\nhost = "b"\n\n'
        "[tail]\nw = 2\n"
    )
    doc = tomlkit.parse(source)
    assert isinstance(doc.item("datasource"), OutOfOrderTableProxy)

    table_to_aot(doc, "datasource")

    expected = (
        "[[datasource]]\n"
        'url = "pg"\n'
        '[[datasource.replicas]]\nhost = "a"\n\n'
        '[[datasource.replicas]]\nhost = "b"\n\n'
        "[other]\nz = 1\n\n"
        "[tail]\nw = 2\n"
    )
    assert tomlkit.dumps(doc) == expected
    assert doc.unwrap()["datasource"] == [
        {"url": "pg", "replicas": [{"host": "a"}, {"host": "b"}]}
    ]


def test_migration_works_on_a_nested_parent_container() -> None:
    source = (
        '[parent]\nname = "p"\n'
        '[parent.datasource]\nurl = "pg"\n'
        "[parent.datasource.pool]\nmax = 10\n"
    )
    doc = tomlkit.parse(source)

    table_to_aot(doc.item("parent"), "datasource")

    assert tomlkit.dumps(doc) == (
        "[parent]\n"
        'name = "p"\n'
        "[[parent.datasource]]\n"
        'url = "pg"\n'
        "[parent.datasource.pool]\nmax = 10\n"
    )


def test_quoted_and_key_object_names_are_accepted() -> None:
    doc = tomlkit.parse('["data source"]\nurl = "pg"\n')
    table_to_aot(doc, SingleKey("data source"))
    assert tomlkit.dumps(doc) == '[["data source"]]\nurl = "pg"\n'


@pytest.mark.parametrize(
    "source",
    [
        '[[datasource]]\nurl = "pg"\n',
        'datasource = { url = "pg" }\n',
        "datasource = 5\n",
    ],
)
def test_non_plain_table_is_rejected(source: str) -> None:
    doc = tomlkit.parse(source)
    before = doc.item("datasource")
    with pytest.raises(MigrationError):
        table_to_aot(doc, "datasource")
    # The original item is still the very same object; nothing was converted.
    assert doc.item("datasource") is before


def test_missing_key_is_rejected() -> None:
    doc = tomlkit.parse("[other]\nz = 1\n")
    with pytest.raises(MigrationError, match="no table named"):
        table_to_aot(doc, "datasource")


def test_dotted_key_name_is_rejected() -> None:
    doc = tomlkit.parse("[datasource.pool]\nmax = 10\n")
    with pytest.raises(MigrationError, match="single key"):
        table_to_aot(doc, tomlkit.key(["datasource", "pool"]))


def test_own_header_fragment_is_rejected_without_touching_the_document() -> None:
    source = '[datasource]\nurl = "pg"\n[other]\nz = 1\n[datasource.pool]\nmax = 10\n'
    doc = tomlkit.parse(source)
    proxy = doc.item("datasource")
    assert isinstance(proxy, OutOfOrderTableProxy)
    # Simulate a later same-name fragment that opens its own header: there is no
    # rule telling us which source it belongs to.
    proxy._tables[1]._is_super_table = False

    with pytest.raises(MigrationError, match="own header"):
        table_to_aot(doc, "datasource")

    # The original table/proxy is still in place; nothing was converted.
    assert isinstance(doc.item("datasource"), OutOfOrderTableProxy)
    assert not isinstance(doc.item("datasource"), AoT)


def test_merge_failure_is_wrapped_and_leaves_the_document_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tomlkit.container import Container

    # An out-of-order source forces the migration through the fragment-merge
    # loop, where a redefinition would surface while building the element.
    source = '[datasource]\nurl = "pg"\n[other]\nz = 1\n[datasource.pool]\nmax = 10\n'
    doc = tomlkit.parse(source)
    before_dump = tomlkit.dumps(doc)

    def fail(*_a: object, **_kw: object) -> object:
        raise TOMLKitError("redefined")

    monkeypatch.setattr(Container, "append", fail)

    with pytest.raises(MigrationError, match="redefines an existing key"):
        table_to_aot(doc, "datasource")

    # The assignment never happens: the out-of-order table stays a proxy and
    # the document renders exactly as before the failed migration.
    assert isinstance(doc.item("datasource"), OutOfOrderTableProxy)
    assert tomlkit.dumps(doc) == before_dump


def test_a_successful_migration_is_not_applied_twice() -> None:
    doc = tomlkit.parse('[datasource]\nurl = "pg"\n')
    table_to_aot(doc, "datasource")
    with pytest.raises(MigrationError):
        table_to_aot(doc, "datasource")
