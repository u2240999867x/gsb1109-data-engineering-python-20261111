from typing import cast

import pytest

from tomlkit import Position
from tomlkit import SourceRange
from tomlkit import load
from tomlkit import loads
from tomlkit import parse
from tomlkit.container import OutOfOrderTableProxy
from tomlkit.items import AoT
from tomlkit.items import Table
from tomlkit.source import Source
from tomlkit.source import SourceTracker


def test_ordinary_parse_records_no_spans() -> None:
    doc = parse("a = 1\n")

    assert doc["a"].range is None
    assert doc["a"].ranges == ()
    assert doc["a"].original_ranges() == ()
    assert doc["a"].is_range_valid() is False


def test_tracking_can_be_enabled_via_loads_alias() -> None:
    doc = loads("a = 1\n", track_sources=True)

    source_range = doc["a"].range
    assert source_range is not None
    assert doc["a"].is_range_valid() is True


def test_load_forwards_tracking_option() -> None:
    import io

    doc = load(io.StringIO("a = 1\n"), track_sources=True)

    assert doc["a"].range is not None


def test_position_and_range_coordinate_conventions() -> None:
    source = "a = 1\nb = 2\n"
    doc = parse(source, track_sources=True)

    source_range = doc["a"].range
    assert source_range is not None
    # Zero-based character offsets, half-open, slicing reproduces the token.
    assert source[source_range.as_slice()] == "1"
    assert source_range.start == Position(index=4, line=1, column=4)
    assert source_range.end == Position(index=5, line=1, column=5)
    # Lines are one-based, columns zero-based.
    second = doc["b"].range
    assert second is not None
    assert second.start.line == 2
    assert second.start.column == 4


def test_columns_count_tabs_as_one_character() -> None:
    source = "b\t= 2\n"
    doc = parse(source, track_sources=True)

    source_range = doc["b"].range
    assert source_range is not None
    assert source_range.start.column == 4
    assert source[source_range.as_slice()] == "2"


def test_crlf_counts_as_a_single_line_break() -> None:
    source = "a = 1\r\nb = 2\r\n"
    doc = parse(source, track_sources=True)

    first = doc["a"].range
    second = doc["b"].range
    assert first is not None and second is not None
    assert first.start.line == 1
    assert second.start.line == 2
    assert source[second.as_slice()] == "2"


def test_multiline_string_keeps_a_multi_line_range() -> None:
    source = 's = """\nline two\nline three"""\n'
    doc = parse(source, track_sources=True)

    source_range = doc["s"].range
    assert source_range is not None
    assert source_range.start.line == 1
    assert source_range.end.line == 3
    # The span includes the triple quotes and every line in between.
    assert source[source_range.as_slice()] == '"""\nline two\nline three"""'


def test_bare_and_quoted_keys_have_their_own_spans() -> None:
    source = 'bare = 1\n"quoted" = 2\n'
    doc = parse(source, track_sources=True)

    bare_key = doc.body[0][0]
    quoted_key = doc.body[1][0]
    assert bare_key is not None and quoted_key is not None
    bare_range = bare_key.range
    quoted_range = quoted_key.range
    assert bare_range is not None and quoted_range is not None
    assert source[bare_range.as_slice()] == "bare"
    assert source[quoted_range.as_slice()] == '"quoted"'


def test_dotted_key_fragments_are_separate_spans() -> None:
    source = "a.b.c = 1\n"
    doc = parse(source, track_sources=True)

    composite = doc.body[0][0]
    assert composite is not None
    # The composite key object stored in the document reports every fragment,
    # expressing that the dotted key is not one contiguous token.
    fragments = [source[r.as_slice()] for r in composite.ranges]
    assert fragments == ["a", "b", "c"]

    # Every intermediate fragment keeps its own location inside the
    # generated super tables rather than one span covering the whole line.
    table_a = cast(Table, doc.item("a"))
    key_b = table_a.value.body[0][0]
    assert key_b is not None
    b_range = key_b.range
    assert b_range is not None
    assert source[b_range.as_slice()] == "b"

    table_b = cast(Table, table_a.item("b"))
    key_c = table_b.value.body[0][0]
    assert key_c is not None
    c_range = key_c.range
    assert c_range is not None
    assert source[c_range.as_slice()] == "c"


def test_table_header_spans_its_brackets() -> None:
    source = "[server]\nport = 80\n"
    doc = parse(source, track_sources=True)

    table = doc.item("server")
    source_range = table.range
    assert source_range is not None
    assert source[source_range.as_slice()] == "[server]"
    assert table.ranges == (source_range,)


def test_array_of_tables_exposes_every_header_span() -> None:
    source = '[[fruit]]\nname = "a"\n[[fruit]]\nname = "b"\n'
    doc = parse(source, track_sources=True)

    aot = cast(AoT, doc.item("fruit"))
    spans = [source[r.as_slice()] for r in aot.ranges]
    assert spans == ["[[fruit]]", "[[fruit]]"]
    # Each element table keeps the header that opened it.
    first = aot[0].range
    second = aot[1].range
    assert first is not None and second is not None
    assert source[first.as_slice()] == "[[fruit]]"
    assert source[second.as_slice()] == "[[fruit]]"


def test_separated_aot_segments_all_report_their_headers() -> None:
    source = '[[fruit]]\nname = "a"\n[other]\nx = 1\n[[fruit]]\nname = "b"\n'
    doc = parse(source, track_sources=True)

    aot = cast(AoT, doc.item("fruit"))
    spans = [source[r.as_slice()] for r in aot.ranges]
    assert spans == ["[[fruit]]", "[[fruit]]"]


def test_out_of_order_table_aggregates_discontinuous_declarations() -> None:
    source = "[a.x]\np = 1\n[foo]\nbar = 2\n[a.y]\nq = 3\n"
    doc = parse(source, track_sources=True)

    proxy = doc.item("a")
    assert isinstance(proxy, OutOfOrderTableProxy)
    spans = [source[r.as_slice()] for r in proxy.ranges]
    assert spans == ["[a.x]", "[a.y]"]
    assert proxy.is_range_valid() is True
    # The original spans remain available even if one part is invalidated.
    assert len(proxy.original_ranges()) == 2


def test_nested_arrays_and_inline_tables_span_their_tokens() -> None:
    source = "arr = [[1, 2], [3]]\ninline = {x = 1}\n"
    doc = parse(source, track_sources=True)

    arr = doc["arr"]
    assert source[arr.range.as_slice()] == "[[1, 2], [3]]"
    assert source[arr[0].range.as_slice()] == "[1, 2]"
    assert source[arr[0][1].range.as_slice()] == "2"

    inline = doc["inline"]
    assert source[inline.range.as_slice()] == "{x = 1}"
    assert source[inline["x"].range.as_slice()] == "1"


def test_newly_created_items_have_no_fabricated_span() -> None:
    doc = parse("a = 1\n", track_sources=True)
    doc["brand_new"] = 2

    new_item = doc["brand_new"]
    assert new_item.range is None
    assert new_item.original_ranges() == ()
    assert new_item.is_range_valid() is False


def test_replacing_a_value_invalidates_only_the_old_item() -> None:
    source = "a = 1\nb = 2\n"
    doc = parse(source, track_sources=True)
    old = doc["a"]

    doc["a"] = 9

    assert old.range is None
    assert old.is_range_valid() is False
    # The original location is preserved for inspection, just not live.
    assert len(old.original_ranges()) == 1
    assert source[old.original_ranges()[0].as_slice()] == "1"
    assert doc["a"].range is None
    # An untouched sibling keeps its live span.
    assert doc["b"].range is not None


def test_deleting_an_item_marks_its_span_stale() -> None:
    source = "a = 1\n"
    doc = parse(source, track_sources=True)
    removed = doc["a"]

    del doc["a"]

    assert removed.range is None
    assert removed.is_range_valid() is False
    assert len(removed.original_ranges()) == 1


def test_editing_an_array_invalidates_its_span_and_the_element() -> None:
    source = "arr = [10, 20]\n"
    doc = parse(source, track_sources=True)
    arr = doc["arr"]
    element = arr[0]

    arr[0] = 11

    assert element.range is None
    assert len(element.original_ranges()) == 1
    assert arr.range is None
    assert len(arr.original_ranges()) == 1
    # The untouched second element is still located.
    assert source[arr[1].range.as_slice()] == "20"


def test_array_insert_and_delete_invalidate_the_whole_array() -> None:
    doc = parse("arr = [1, 2]\n", track_sources=True)
    arr = doc["arr"]

    arr.insert(0, 0)
    assert arr.range is None

    doc2 = parse("arr = [1, 2]\n", track_sources=True)
    arr2 = doc2["arr"]
    del arr2[0]
    assert arr2.range is None


def test_aot_edits_invalidate_only_removed_element() -> None:
    source = '[[fruit]]\nname = "a"\n[[fruit]]\nname = "b"\n'
    doc = parse(source, track_sources=True)
    aot = doc["fruit"]
    removed = aot[0]

    del aot[0]

    assert removed.range is None
    assert len(removed.original_ranges()) == 1
    # The untouched remaining element still reports its own header.
    assert [source[r.as_slice()] for r in aot.ranges] == ["[[fruit]]"]
    assert aot.range is not None


def test_untracked_source_cannot_build_ranges() -> None:
    parser_source = Source("a = 1\n")

    assert parser_source.tracker is None
    assert parser_source.is_tracking() is False
    with pytest.raises(RuntimeError):
        parser_source.make_range(0, 1)


def test_tracker_builds_line_index_lazily_per_document() -> None:
    tracker = SourceTracker()
    assert tracker._line_starts is None

    tracker.build_line_index("a\nb\r\nc\rd")
    position = tracker.position(5)

    # Offset 5 sits on the third line ("c"), zero-based column zero.
    assert position == Position(index=5, line=3, column=0)
    assert tracker.range(0, 1) == SourceRange(Position(0, 1, 0), Position(1, 1, 1))


def test_range_str_uses_line_column_pairs() -> None:
    source_range = SourceRange(Position(0, 1, 0), Position(3, 1, 3))
    assert str(source_range) == "1:0-1:3"


def test_character_offsets_convert_to_utf8_byte_offsets() -> None:
    from tomlkit.source import byte_offset

    source = 'k = "héllo"\n'
    doc = parse(source, track_sources=True)
    source_range = doc["k"].range
    assert source_range is not None

    # Character offsets index the decoded str...
    assert source[source_range.as_slice()] == '"héllo"'
    # ...while byte_slice addresses the same text in a UTF-8 buffer.
    encoded = source.encode("utf-8")
    assert encoded[source_range.byte_slice(source)] == b'"h\xc3\xa9llo"'
    assert source_range.start.index == 4
    assert byte_offset(source, source_range.start.index) == 4
    # "héllo" contains one two-byte character, so the byte end is one further.
    assert byte_offset(source, source_range.end.index) == source_range.end.index + 1
