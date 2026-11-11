import pytest

from strictyaml import (
    Any,
    Bool,
    CommaSeparated,
    DefaultChange,
    FixedSeq,
    Int,
    LoadFailure,
    Map,
    MapPattern,
    MigrationApplyError,
    Optional,
    RejectedPosition,
    Seq,
    Str,
    TypeChange,
    load,
    preview_migration,
)


DATA_SOURCE_YAML = """\
# data sources
sources:
  - name: orders
    enabled: yes
    timeout: 30
  - name: clicks
    enabled: no
"""


def old_source_schema():
    return Map(
        {
            "sources": Seq(
                Map(
                    {
                        "name": Str(),
                        Optional("enabled", default="yes"): Str(),
                        Optional("timeout", default="60"): Str(),
                    }
                )
            )
        }
    )


def new_source_schema():
    return Map(
        {
            "sources": Seq(
                Map(
                    {
                        "name": Str(),
                        Optional("enabled", default=True): Bool(),
                        Optional("timeout", default=60): Int(),
                    }
                )
            )
        }
    )


def test_text_yes_becomes_boolean_type_change():
    preview = preview_migration(
        DATA_SOURCE_YAML, old_source_schema(), new_source_schema()
    )

    enabled_changes = [
        change for change in preview.type_changes if change.path[-1] == "enabled"
    ]
    assert len(enabled_changes) == 2
    for change in enabled_changes:
        assert change.old_type == "str"
        assert change.new_type == "bool"
    assert enabled_changes[0].old_value == "yes"
    assert enabled_changes[0].new_value is True
    assert enabled_changes[1].old_value == "no"
    assert enabled_changes[1].new_value is False
    assert enabled_changes[0].path == ("sources", 0, "enabled")
    assert enabled_changes[0].line == 4


def test_type_change_for_text_integer_becoming_int():
    preview = preview_migration(
        DATA_SOURCE_YAML, old_source_schema(), new_source_schema()
    )
    timeout = next(
        change
        for change in preview.type_changes
        if change.path == ("sources", 0, "timeout")
    )
    assert timeout.old_type == "str"
    assert timeout.new_type == "int"
    assert timeout.old_value == "30"
    assert timeout.new_value == 30


def test_default_change_reported_separately_from_type_change():
    preview = preview_migration(
        DATA_SOURCE_YAML, old_source_schema(), new_source_schema()
    )
    # clicks has no timeout in the source, so only the default differs.
    default_change = next(
        change
        for change in preview.default_changes
        if change.path == ("sources", 1, "timeout")
    )
    assert isinstance(default_change, DefaultChange)
    assert default_change.old_default == "60"
    assert default_change.new_default == 60
    assert default_change.old_type == "str"
    assert default_change.new_type == "int"
    assert not any(
        change.path == ("sources", 1, "timeout") for change in preview.type_changes
    )


def test_load_success_but_meaning_changed_is_distinct_from_load_failure():
    preview = preview_migration(
        DATA_SOURCE_YAML, old_source_schema(), new_source_schema()
    )
    assert preview.loadable is True
    assert preview.old_load_error is None
    assert preview.new_load_error is None
    assert preview.semantic_changes
    assert preview.compatible_with_new_schema is True


def test_preview_does_not_mutate_existing_yaml_object():
    document = load(DATA_SOURCE_YAML, old_source_schema())
    before = document.as_yaml()

    preview_migration(DATA_SOURCE_YAML, old_source_schema(), new_source_schema())

    assert document.as_yaml() == before
    assert document["sources"][0]["enabled"].data == "yes"
    assert preview_migration(
        DATA_SOURCE_YAML, old_source_schema(), new_source_schema()
    ).as_dict() is not None


def test_apply_reparses_original_text_with_new_schema():
    preview = preview_migration(
        DATA_SOURCE_YAML, old_source_schema(), new_source_schema()
    )
    applied = preview.apply()

    assert applied["sources"][0]["enabled"].data is True
    assert applied["sources"][0]["timeout"].data == 30
    assert applied["sources"][1]["enabled"].data is False
    # The materialized default is now an int under the new schema.
    assert applied["sources"][1]["timeout"].data == 60


def test_rejected_leaf_position_when_new_schema_refuses_value():
    yaml_text = (
        "sources:\n"
        "  - name: orders\n"
        "    enabled: maybe\n"
        "    region: eu\n"
    )
    old_schema = Map(
        {
            "sources": Seq(
                Map(
                    {
                        "name": Str(),
                        Optional("enabled"): Str(),
                        Optional("region"): Str(),
                    }
                )
            )
        }
    )
    new_schema = Map({"sources": Seq(Map({"name": Str(), "enabled": Bool()}))})

    preview = preview_migration(yaml_text, old_schema, new_schema)

    assert preview.loadable is True
    assert preview.compatible_with_new_schema is False
    assert isinstance(preview.new_load_error, LoadFailure)

    rejected_paths = {position.path for position in preview.rejected}
    assert ("sources", 0, "enabled") in rejected_paths
    assert ("sources", 0, "region") in rejected_paths
    enabled_rejection = next(
        position
        for position in preview.rejected
        if position.path == ("sources", 0, "enabled")
    )
    assert isinstance(enabled_rejection, RejectedPosition)
    assert enabled_rejection.line == 3
    assert "boolean" in enabled_rejection.message


def test_apply_blocked_while_rejections_exist():
    yaml_text = "enabled: maybe\n"
    preview = preview_migration(
        yaml_text, Map({"enabled": Str()}), Map({"enabled": Bool()})
    )
    with pytest.raises(MigrationApplyError):
        preview.apply()


def test_unloadable_under_old_schema_is_load_failure_without_semantic_changes():
    bad_yaml = "sources:\n  - enabled: yes\n"
    preview = preview_migration(
        bad_yaml, old_source_schema(), new_source_schema()
    )

    assert preview.loadable is False
    assert isinstance(preview.old_load_error, LoadFailure)
    assert preview.old_load_error.error_type == "YAMLValidationError"
    assert preview.type_changes == []
    assert preview.default_changes == []
    assert preview.rejected == []
    with pytest.raises(MigrationApplyError):
        preview.apply()


def test_syntax_error_separated_from_semantic_changes():
    preview = preview_migration(
        "sources:\n  - name: [unclosed\n",
        old_source_schema(),
        new_source_schema(),
    )
    assert preview.loadable is False
    assert preview.old_load_error is not None
    assert preview.old_load_error.line == 2
    assert preview.semantic_changes == []


def test_no_differences_preview():
    preview = preview_migration(
        "name: ford\n", Map({"name": Str()}), Map({"name": Str()})
    )
    assert preview.has_changes is False
    assert preview.type_changes == []
    assert preview.default_changes == []
    assert preview.rejected == []
    assert preview.apply().data == {"name": "ford"}


def test_default_added_and_removed():
    yaml_text = "name: orders\nflag: yes\n"
    old_schema = Map(
        {
            "name": Str(),
            Optional("flag"): Str(),
            Optional("retries", default=3): Int(),
        }
    )
    new_schema = Map(
        {
            "name": Str(),
            "flag": Bool(),
            Optional("retries"): Int(),
            Optional("timeout", default=30): Int(),
        }
    )
    preview = preview_migration(yaml_text, old_schema, new_schema)

    defaults = {change.path[-1]: change for change in preview.default_changes}
    assert defaults["timeout"].old_default_present is False
    assert defaults["timeout"].new_default_present is True
    assert defaults["timeout"].new_default == 30
    assert defaults["retries"].old_default_present is True
    assert defaults["retries"].new_default_present is False
    assert defaults["retries"].old_default == 3

    flag_change = next(
        change for change in preview.type_changes if change.path == ("flag",)
    )
    assert isinstance(flag_change, TypeChange)
    assert flag_change.new_value is True


def test_mappattern_type_changes():
    preview = preview_migration(
        "a: yes\nb: no\n", MapPattern(Str(), Str()), MapPattern(Str(), Bool())
    )
    paths = {change.path: change for change in preview.type_changes}
    assert paths[("a",)].new_value is True
    assert paths[("b",)].new_value is False
    assert preview.compatible_with_new_schema is True


def test_fixedseq_type_changes_by_index():
    preview = preview_migration(
        "- yes\n- 5\n", FixedSeq([Str(), Str()]), FixedSeq([Bool(), Int()])
    )
    by_index = {change.path[0]: change for change in preview.type_changes}
    assert by_index[0].new_type == "bool"
    assert by_index[1].new_type == "int"


def test_or_schema_keeps_first_matching_interpretation():
    # Str() is tried first by OrValidator, so "yes" stays a string.
    preview = preview_migration(
        "flag: yes\n", Map({"flag": Str()}), Map({"flag": Str() | Bool()})
    )
    assert preview.type_changes == []
    assert preview.apply().data == {"flag": "yes"}


def test_as_dict_is_json_friendly():
    import json

    preview = preview_migration(
        DATA_SOURCE_YAML, old_source_schema(), new_source_schema()
    )
    payload = preview.as_dict()
    json.dumps(payload)
    assert payload["loadable"] is True
    assert {
        "type_changes",
        "default_changes",
        "rejected",
        "old_load_error",
        "new_load_error",
    }.issubset(payload)


def test_describe_mentions_each_category():
    yaml_text = "enabled: maybe\n"
    preview = preview_migration(
        yaml_text, Map({"enabled": Str()}), Map({"enabled": Bool()})
    )
    text = preview.describe()
    assert "Rejected by the new schema" in text


def test_comma_separated_to_seq_type_change():
    preview = preview_migration(
        "tags: a, b\n",
        Map({"tags": Str()}),
        Map({"tags": CommaSeparated(Str())}),
    )
    change = preview.type_changes[0]
    assert change.old_type == "str"
    assert change.new_type == "list"
    assert change.new_value == ["a", "b"]


def test_any_schema_round_trip_with_type_change():
    preview = preview_migration(
        "flag: yes\n", Any(), Map({"flag": Bool()})
    )
    assert preview.type_changes[0].new_value is True
    assert preview.compatible_with_new_schema is True


def test_seq_of_scalars_reports_indexed_changes_and_rejections():
    preview = preview_migration(
        "- yes\n- maybe\n", Seq(Str()), Seq(Bool()), label="datasources.yaml"
    )
    assert preview.type_changes[0].path == (0,)
    assert preview.type_changes[0].new_value is True
    rejection = preview.rejected[0]
    assert rejection.path == (1,)
    assert rejection.line == 2
    assert "datasources.yaml" in rejection.message


def test_apply_propagates_label():
    preview = preview_migration(
        "- yes\n", Seq(Str()), Seq(Bool()), label="datasources.yaml"
    )
    applied = preview.apply()
    assert applied.data == [True]
    assert applied.start_line == 1
