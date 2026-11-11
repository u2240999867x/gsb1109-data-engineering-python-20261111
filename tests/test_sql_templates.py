"""Fidelity regressions for SQL templates stored as TOML strings.

The fixtures live in ``examples/sql_templates.toml`` (all supported syntax)
and ``examples/invalid/sql_templates_unsupported.toml`` (one TOML 1.1 spelling
that tomlkit deliberately does not accept yet). Editing a simple boolean
switch next to the templates must leave both the decoded SQL values and the
unmodified original spelling verifiable after re-dumping and re-parsing.
"""

from typing import Callable

import pytest

from tomlkit import dumps
from tomlkit import loads
from tomlkit.exceptions import ParseError
from tomlkit.items import String
from tomlkit.items import StringType


SQL_FIELDS = (
    "mlb_cont",
    "mlb_quote_boundary",
    "mll_literal",
    "slb_quote_boundary",
    "sll_literal",
    "mll_unicode",
)

EXPECTED_SQL = {
    # backslash line continuations collapse whitespace out of the value only
    "mlb_cont": "SELECT id, name FROM users WHERE active = true AND city = '北京'",
    # five source quotes decode to a value ending in two quotes
    "mlb_quote_boundary": 'SELECT \'id\' AS "id", count(*) AS "cnt"  ""',
    # a literal string never interprets backslash escapes
    "mll_literal": "SELECT length('a\\nb') AS len, '\\t' AS tab FROM dual",
    # escaped quotes terminate right before the closing basic quote
    "slb_quote_boundary": 'SELECT * FROM t WHERE note = "雪 ☃" AND mark = ☃',
    "sll_literal": 'SELECT \\n AS backslash_n, "name" AS name FROM dual',
    "mll_unicode": (
        "SELECT 城市, count(*) AS 数量\n  FROM 订单\n  WHERE 状态 = '已付款' "
    ),
}

EXPECTED_TYPE = {
    "mlb_cont": StringType.MLB,
    "mlb_quote_boundary": StringType.MLB,
    "mll_literal": StringType.MLL,
    "slb_quote_boundary": StringType.SLB,
    "sll_literal": StringType.SLL,
    "mll_unicode": StringType.MLL,
}


@pytest.mark.parametrize("field", SQL_FIELDS)
def test_sql_templates_decode_to_expected_values_and_keep_spelling(
    example: Callable[[str], str], field: str
) -> None:
    doc = loads(example("sql_templates"))
    item = doc["query"][field]

    assert isinstance(item, String)
    assert item.type is EXPECTED_TYPE[field]
    # The decoded SQL value is available for direct semantic checks.
    assert str(item) == EXPECTED_SQL[field]

    raw = item.as_string()
    if field == "mlb_cont":
        # line continuation: a literal backslash followed by a newline
        assert "\\\n" in raw
        assert raw.startswith('"""')
    elif field == "mlb_quote_boundary":
        # two body quotes plus the closing triple quote share the boundary
        assert raw.endswith('"""""')
    elif field == "mll_literal":
        assert raw.startswith("'''")
        assert r"\n" in raw and r"\t" in raw
    elif field == "slb_quote_boundary":
        assert r"\"" in raw
    elif field == "sll_literal":
        assert raw.startswith("'") and raw.endswith("'")
        assert r"\n" in raw and '"name"' in raw
    else:
        assert raw.startswith("'''")
        assert "\n" in raw


def test_flipping_switch_preserves_every_sql_template(
    example: Callable[[str], str],
) -> None:
    content = example("sql_templates")
    doc = loads(content)

    raw_before = {field: doc["query"][field].as_string() for field in SQL_FIELDS}
    decoded_before = {field: str(doc["query"][field]) for field in SQL_FIELDS}

    # The edit touches only the simple switch sitting next to the templates.
    assert doc["dry_run"] is False
    doc["dry_run"] = True

    redumped = dumps(doc)
    reread = loads(redumped)

    assert reread["dry_run"] is True
    before_lines = content.splitlines()
    after_lines = redumped.splitlines()
    assert len(before_lines) == len(after_lines)
    differing = [
        (old, new) for old, new in zip(before_lines, after_lines) if old != new
    ]
    assert differing == [("dry_run = false", "dry_run = true")]

    for field in SQL_FIELDS:
        item = reread["query"][field]
        assert isinstance(item, String)
        # decoded SQL stays identical after the re-dump/re-parse cycle
        assert str(item) == decoded_before[field]
        # the unmodified original spelling is still available verbatim
        assert item.as_string() == raw_before[field]

    # flipping the switch back restores the source byte-for-byte
    reread["dry_run"] = False
    assert dumps(reread) == content


def test_unsupported_toml11_special_float_remains_out_of_scope(
    invalid_example: Callable[[str], str],
) -> None:
    content = invalid_example("sql_templates_unsupported")

    # The unsupported spelling is kept in the sample on purpose: the scope of
    # the failure must stay explicit instead of deleting the case.
    with pytest.raises(ParseError) as exc_info:
        loads(content)
    assert "'N'" in str(exc_info.value)
    assert "NaN" in content

    # Everything else in the same sample (switch + SQL templates) is valid:
    # replacing the single unsupported token makes the whole document parse
    # and round-trip exactly.
    fixed = content.replace("threshold = NaN", "threshold = nan")
    doc = loads(fixed)
    assert dumps(doc) == fixed

    doc["dry_run"] = True
    reread = loads(dumps(doc))
    assert reread["dry_run"] is True
    for field in ("mlb_cont", "slb_quote_boundary", "sll_literal", "mll_unicode"):
        assert str(reread["query"][field]) == str(doc["query"][field])


def test_toml11_escape_sequences_in_sql_are_within_supported_scope() -> None:
    # Unlike the capitalized special float, the TOML 1.1 ``\e`` and ``\x``
    # escapes are supported and must preserve their original spelling while a
    # sibling switch is edited.
    content = (
        "dry_run = false\n"
        'sql = "SELECT \\e AS esc, \\x41 AS hex"\n'
        "multi = '''line1\\nline2'''\n"
    )
    doc = loads(content)
    assert str(doc["sql"]) == "SELECT \x1b AS esc, A AS hex"

    doc["dry_run"] = True
    reread = loads(dumps(doc))
    assert reread["dry_run"] is True
    assert reread["sql"].as_string() == r'"SELECT \e AS esc, \x41 AS hex"'
    assert reread["multi"].as_string() == "'''line1\\nline2'''"
    assert dumps(reread).replace("dry_run = true", "dry_run = false") == content
