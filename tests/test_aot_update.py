from __future__ import annotations

import pytest

import tomlkit

from tomlkit.exceptions import NonExistentKey
from tomlkit.exceptions import NonUniqueKeyError


SOURCES = """\
[[sources]]
name = "main"
url = "https://main.example"  # primary endpoint
# managed by hand, do not drop
retries = 3

[[sources]]
name = "backup"
url = "https://backup.example"

[[sources]]
name = "archive"
url = "https://archive.example"
"""


def test_update_aot_item_finds_element_by_key_not_position() -> None:
    doc = tomlkit.parse(SOURCES)

    updated = tomlkit.update_aot_item(
        doc["sources"], "name", "backup", {"url": "https://backup2.example"}
    )

    assert updated["url"] == "https://backup2.example"
    assert [source["name"] for source in doc["sources"]] == [
        "main",
        "backup",
        "archive",
    ]
    assert doc["sources"][0]["url"] == "https://main.example"
    assert doc["sources"][2]["url"] == "https://archive.example"


def test_update_aot_item_preserves_comments_and_unknown_fields() -> None:
    doc = tomlkit.parse(SOURCES)

    tomlkit.update_aot_item(
        doc["sources"], "name", "main", {"url": "https://main2.example"}
    )

    rendered = tomlkit.dumps(doc)
    assert "# primary endpoint" in rendered
    assert "# managed by hand, do not drop" in rendered
    assert "retries = 3" in rendered
    reparsed = tomlkit.parse(rendered)
    assert reparsed["sources"][0]["url"] == "https://main2.example"
    assert reparsed["sources"][0]["retries"] == 3


def test_update_aot_item_can_add_new_fields_to_matched_element() -> None:
    doc = tomlkit.parse(SOURCES)

    tomlkit.update_aot_item(
        doc["sources"], "name", "archive", {"timeout": 30, "enabled": True}
    )

    archive = doc["sources"][2]
    assert archive["timeout"] == 30
    assert archive["enabled"] is True
    assert archive["url"] == "https://archive.example"
    tomlkit.parse(tomlkit.dumps(doc))


def test_update_aot_item_raises_on_conflicting_matches() -> None:
    doc = tomlkit.parse(
        '[[sources]]\nname = "dup"\n\n[[sources]]\nname = "dup"\n'
    )

    with pytest.raises(NonUniqueKeyError):
        tomlkit.update_aot_item(doc["sources"], "name", "dup", {"url": "x"})


def test_update_aot_item_raises_for_missing_element_by_default() -> None:
    doc = tomlkit.parse(SOURCES)

    with pytest.raises(NonExistentKey):
        tomlkit.update_aot_item(doc["sources"], "name", "missing", {"url": "x"})


def test_update_aot_item_creates_element_when_asked() -> None:
    doc = tomlkit.parse(SOURCES)

    created = tomlkit.update_aot_item(
        doc["sources"],
        "name",
        "metrics",
        {"url": "https://metrics.example"},
        create=True,
    )

    assert created["name"] == "metrics"
    assert [source["name"] for source in doc["sources"]] == [
        "main",
        "backup",
        "archive",
        "metrics",
    ]
    reparsed = tomlkit.parse(tomlkit.dumps(doc))
    assert reparsed["sources"][3]["url"] == "https://metrics.example"


def test_update_aot_item_create_keeps_existing_elements_untouched() -> None:
    doc = tomlkit.parse(SOURCES)
    before = [source["url"] for source in doc["sources"]]

    tomlkit.update_aot_item(
        doc["sources"], "name", "metrics", {"url": "https://m"}, create=True
    )

    assert [source["url"] for source in doc["sources"]][:3] == before
    rendered = tomlkit.dumps(doc)
    assert "# primary endpoint" in rendered
    tomlkit.parse(rendered)


def test_update_aot_item_rejects_non_aot_values() -> None:
    doc = tomlkit.parse("x = 1\n")

    with pytest.raises(TypeError):
        tomlkit.update_aot_item(doc["x"], "name", "main", {})
