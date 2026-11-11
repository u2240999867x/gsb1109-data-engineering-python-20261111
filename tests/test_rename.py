from __future__ import annotations

import pytest

import tomlkit

from tomlkit import key
from tomlkit import parse
from tomlkit.exceptions import KeyRenameError
from tomlkit.exceptions import NonExistentKey


def _assert_round_trips(doc: tomlkit.TOMLDocument) -> None:
    rendered = doc.as_string()
    assert parse(rendered).unwrap() == doc.unwrap()


def test_rename_dotted_key_at_root_preserves_value_and_comment() -> None:
    content = "storage.timeout = 30 # seconds\nother = 1\n"
    doc = parse(content)

    doc.rename("storage.timeout", "storage.request_timeout")

    expected = "storage.request_timeout = 30 # seconds\nother = 1\n"
    assert doc.as_string() == expected
    assert "storage.timeout" not in doc.as_string()
    _assert_round_trips(doc)


def test_rename_leaf_under_table_header() -> None:
    content = "[storage]\ntimeout = 30 # seconds\nkeep = 2\n"
    doc = parse(content)

    doc.rename(("storage", "timeout"), ("storage", "request_timeout"))

    expected = "[storage]\nrequest_timeout = 30 # seconds\nkeep = 2\n"
    assert doc.as_string() == expected
    _assert_round_trips(doc)


def test_rename_accepts_key_objects() -> None:
    doc = parse("storage.timeout = 30\n")

    doc.rename(key(["storage", "timeout"]), key(["storage", "request_timeout"]))

    assert doc.as_string() == "storage.request_timeout = 30\n"
    _assert_round_trips(doc)


def test_rename_keeps_spacing_around_separator() -> None:
    content = "[storage]\n  timeout  =  30 # c\n"
    doc = parse(content)

    doc.rename(("storage", "timeout"), ("storage", "request_timeout"))

    assert doc.as_string() == "[storage]\n  request_timeout  =  30 # c\n"


def test_rename_preserves_crlf_line_endings() -> None:
    content = "[storage]\r\ntimeout = 30 # c\r\nkeep = 2\r\n"
    doc = parse(content)

    doc.rename("storage.timeout", "storage.request_timeout")

    assert doc.as_string() == (
        "[storage]\r\nrequest_timeout = 30 # c\r\nkeep = 2\r\n"
    )
    _assert_round_trips(doc)


def test_rename_preserves_complex_value_formatting() -> None:
    content = "storage.timeout = [1, 2,\n  3,] # c\nother = 1\n"
    doc = parse(content)

    doc.rename("storage.timeout", "storage.request_timeout")

    assert doc.as_string() == (
        "storage.request_timeout = [1, 2,\n  3,] # c\nother = 1\n"
    )
    _assert_round_trips(doc)


def test_rename_dotted_key_multiple_levels() -> None:
    doc = parse("a.b.c = 1 # deep\n")

    doc.rename("a.b.c", "a.b.d")

    assert doc.as_string() == "a.b.d = 1 # deep\n"
    _assert_round_trips(doc)


def test_rename_quoted_single_key() -> None:
    doc = parse('"weird.key" = 30 # q\n')

    doc.rename(("weird.key",), ("weirder.key",))

    # A key containing a dot stays a single quoted fragment rather than being
    # read as the two-part dotted path weirder . key.
    assert doc.as_string() == '"weirder.key" = 30 # q\n'
    _assert_round_trips(doc)


def test_rename_inline_table_leaf() -> None:
    doc = parse("t = {timeout = 30}\n")

    doc["t"].rename("timeout", "request_timeout")

    assert doc.as_string() == "t = {request_timeout = 30}\n"
    _assert_round_trips(doc)


def test_rename_table_method_on_table() -> None:
    doc = parse("[storage]\ntimeout = 30 # c\n")

    doc["storage"].rename("timeout", "request_timeout")

    assert doc.as_string() == "[storage]\nrequest_timeout = 30 # c\n"


def test_rename_target_already_exists_leaves_document_untouched() -> None:
    content = "storage.timeout = 30\nstorage.request_timeout = 5\n"
    doc = parse(content)

    with pytest.raises(KeyRenameError):
        doc.rename("storage.timeout", "storage.request_timeout")

    assert doc.as_string() == content


def test_rename_target_under_existing_header_leaves_document_untouched() -> None:
    # The implicit table created by the dotted key would be redefined by the
    # existing [storage.request_timeout] header.
    content = "storage.timeout = 30\n[storage.request_timeout]\nx = 1\n"
    doc = parse(content)

    with pytest.raises(KeyRenameError):
        doc.rename("storage.timeout", "storage.request_timeout")

    assert doc.as_string() == content


def test_rename_target_leaf_under_header_collision() -> None:
    content = "[storage]\ntimeout = 30\nrequest_timeout = 5\n"
    doc = parse(content)

    with pytest.raises(KeyRenameError):
        # The pre-check reports the occupied destination before any mutation.
        doc.rename(("storage", "timeout"), ("storage", "request_timeout"))

    assert doc.as_string() == content


def test_rename_into_path_blocked_by_scalar_leaves_document_untouched() -> None:
    content = "storage = 1\n"
    doc = parse(content)

    with pytest.raises(KeyRenameError):
        doc.rename("storage", "storage.request_timeout")

    assert doc.as_string() == content


def test_rename_missing_source_leaves_document_untouched() -> None:
    content = "a = 1\n"
    doc = parse(content)

    with pytest.raises(NonExistentKey):
        doc.rename("storage.timeout", "storage.request_timeout")

    assert doc.as_string() == content


def test_rename_to_same_name_is_a_collision() -> None:
    doc = parse("a = 1\n")
    before = doc.as_string()

    with pytest.raises(KeyRenameError):
        doc.rename("a", "a")

    assert doc.as_string() == before


def test_rename_empty_path_is_rejected() -> None:
    doc = parse("a = 1\n")
    before = doc.as_string()

    with pytest.raises(ValueError):
        doc.rename((), "b")

    assert doc.as_string() == before


def test_rename_moves_value_into_new_table_branch() -> None:
    content = "timeout = 30 # seconds\nother = 1\n"
    doc = parse(content)

    doc.rename("timeout", "storage.request_timeout")

    assert doc.as_string() == (
        "other = 1\n\n[storage]\nrequest_timeout = 30 # seconds\n"
    )
    _assert_round_trips(doc)


def test_rename_moves_leaf_between_tables() -> None:
    content = "[a]\nx = 1\n[b]\ny = 2 # keep\n"
    doc = parse(content)

    doc.rename(("a", "x"), ("b", "x"))

    assert doc.unwrap() == {"a": {}, "b": {"y": 2, "x": 1}}
    _assert_round_trips(doc)
    assert "# keep" in doc.as_string()


def test_rename_moving_last_leaf_prunes_empty_implicit_table() -> None:
    doc = parse("a.b.c = 1\n")

    doc.rename("a.b.c", "a.x.c")

    assert doc.unwrap() == {"a": {"x": {"c": 1}}}
    _assert_round_trips(doc)


def test_rename_moving_last_leaf_keeps_explicit_header() -> None:
    content = "[a]\nx = 1 # c\n[b]\ny = 2\n"
    doc = parse(content)

    doc.rename(("a", "x"), ("b", "x2"))

    assert "[a]" in doc.as_string()
    assert doc.unwrap() == {"a": {}, "b": {"y": 2, "x2": 1}}
    _assert_round_trips(doc)


def test_rename_table_header() -> None:
    content = "[storage]\ntimeout = 30\n[other]\nx = 1\n"
    doc = parse(content)

    doc.rename("storage", "cache")

    assert doc.as_string() == "[cache]\ntimeout = 30\n[other]\nx = 1\n"
    _assert_round_trips(doc)


def test_rename_table_header_keeps_header_comment() -> None:
    content = "[storage] # head\ntimeout = 30\n"
    doc = parse(content)

    doc.rename("storage", "cache")

    assert doc.as_string() == "[cache] # head\ntimeout = 30\n"
    _assert_round_trips(doc)


def test_rename_nested_table_updates_sub_headers() -> None:
    content = "[a.b]\nx = 1\n[a.c]\ny = 2\n"
    doc = parse(content)

    doc.rename("a.b", "a.d")

    assert doc.as_string() == "[a.d]\nx = 1\n[a.c]\ny = 2\n"
    _assert_round_trips(doc)


def test_rename_top_table_with_nested_sub_headers() -> None:
    content = "[a.b]\nx = 1\n[a.c]\ny = 2\n"
    doc = parse(content)

    doc.rename("a", "z")

    assert doc.as_string() == "[z.b]\nx = 1\n[z.c]\ny = 2\n"
    _assert_round_trips(doc)


def test_rename_array_of_tables() -> None:
    content = "[[a.b]]\nx = 1\n[[a.b]]\nx = 2\n"
    doc = parse(content)

    doc.rename("a.b", "a.c")

    assert doc.as_string() == "[[a.c]]\nx = 1\n[[a.c]]\nx = 2\n"
    _assert_round_trips(doc)


def test_rename_leaf_inside_array_of_tables_element() -> None:
    content = "[[fruit]]\nname = 'apple' # n\n[[fruit]]\nname = 'banana'\n"
    doc = parse(content)

    doc.rename(("fruit", 0, "name"), ("fruit", 0, "kind"))

    assert doc.as_string() == (
        "[[fruit]]\nkind = 'apple' # n\n[[fruit]]\nname = 'banana'\n"
    )
    _assert_round_trips(doc)


def test_rename_leaf_inside_second_array_of_tables_element() -> None:
    content = "[[fruit]]\nname = 'apple'\n[[fruit]]\nname = 'banana'\n"
    doc = parse(content)

    doc.rename(("fruit", 1, "name"), ("fruit", 1, "kind"))

    assert doc.as_string() == (
        "[[fruit]]\nname = 'apple'\n[[fruit]]\nkind = 'banana'\n"
    )
    _assert_round_trips(doc)


def test_rename_nested_table_inside_array_of_tables_element() -> None:
    content = "[[servers]]\n[servers.addr]\nhost = 'h' # h\n"
    doc = parse(content)

    doc.rename(
        ("servers", 0, "addr", "host"),
        ("servers", 0, "addr", "hostname"),
    )

    assert doc.as_string() == "[[servers]]\n[servers.addr]\nhostname = 'h' # h\n"
    _assert_round_trips(doc)


def test_rename_with_bad_array_of_tables_index() -> None:
    content = "[[fruit]]\nname = 'a'\n"
    doc = parse(content)
    before = doc.as_string()

    with pytest.raises(NonExistentKey):
        doc.rename(("fruit", 5, "name"), ("fruit", 5, "kind"))

    assert doc.as_string() == before


def test_rename_out_of_order_table_leaf() -> None:
    content = "[a.x]\np = 1 # c\n[foo]\nbar = 2\n[a.y]\nq = 3\n"
    doc = parse(content)

    doc.rename(("a", "x", "p"), ("a", "x", "r"))

    assert doc.as_string() == "[a.x]\nr = 1 # c\n[foo]\nbar = 2\n[a.y]\nq = 3\n"
    _assert_round_trips(doc)
    assert doc["a"]["x"]["r"] == 1


def test_rename_table_spread_over_out_of_order_parts() -> None:
    content = "a.b.c = 1\na.b.d = 2\n"
    doc = parse(content)

    doc.rename("a.b", "a.x")

    assert doc.as_string() == "a.x.c = 1\na.x.d = 2\n"
    _assert_round_trips(doc)
    assert doc.unwrap() == {"a": {"x": {"c": 1, "d": 2}}}


def test_rename_table_under_out_of_order_parent() -> None:
    content = "[a.x]\np = 1\n[foo]\nbar = 2\n[a.y]\nq = 3\n"
    doc = parse(content)

    doc.rename(("a", "x"), ("a", "z"))

    assert doc.as_string() == "[a.z]\np = 1\n[foo]\nbar = 2\n[a.y]\nq = 3\n"
    _assert_round_trips(doc)


def test_rename_dotted_key_into_existing_super_table() -> None:
    # Reopening a header-less super table with another dotted key is valid TOML.
    content = "storage.timeout = 30\n[storage.other]\nx = 1\n"
    doc = parse(content)

    doc.rename("storage.timeout", "storage.request_timeout")

    assert doc.as_string() == (
        "storage.request_timeout = 30\n[storage.other]\nx = 1\n"
    )
    _assert_round_trips(doc)


def test_rename_moves_leaf_out_of_out_of_order_table() -> None:
    content = "[a.x]\np = 1\n[foo]\nbar = 2\n[a.y]\nq = 3\n"
    doc = parse(content)

    doc.rename(("a", "x", "p"), ("foo", "p"))

    assert doc.unwrap() == {
        "a": {"x": {}, "y": {"q": 3}},
        "foo": {"bar": 2, "p": 1},
    }
    _assert_round_trips(doc)


def test_rename_collision_between_tables_leaves_document_untouched() -> None:
    content = "[a]\nx=1\n[b]\ny=2\n"
    doc = parse(content)

    with pytest.raises(KeyRenameError):
        doc.rename(("a", "x"), ("b", "y"))

    assert doc.as_string() == content


def test_rename_does_not_re_encode_through_dict() -> None:
    # A same-container rename only rewrites the key token; every byte outside
    # that token (including comments and blank lines) must stay identical.
    content = (
        "# leading comment\n\n"
        "storage.timeout = 30 # trailing\n"
        "# between\n"
        "other = 1\n"
    )
    doc = parse(content)

    doc.rename("storage.timeout", "storage.request_timeout")

    assert doc.as_string() == content.replace(
        "storage.timeout", "storage.request_timeout", 1
    )
