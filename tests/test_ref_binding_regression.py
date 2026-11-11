"""
Regression tests for reference binding and compilation order in the generator.

These tests exercise three areas where generated code can disagree with the
JSON Schema specification:

1. Two external documents that each define a sub-object under the same name.
2. The same reference reused from different positions in one root.
3. One root compiled several times in a row with different options.

Every remote ``$ref`` is served from an in-memory dictionary through a custom
``handlers`` mapping, so no test performs any network access. Where useful the
generated validators are cross-checked against the reference ``jsonschema``
implementation; those checks are skipped when the reference library is absent.

A few observed differences are allowed by the specification (the standard does
not mandate how generated helper functions are named or deduplicated); they are
documented inline rather than treated as failures.
"""

import copy
import importlib
import sys

import pytest

from fastjsonschema import (
    JsonSchemaDefinitionException,
    JsonSchemaValueException,
    JsonSchemaValuesException,
    compile,
    compile_to_code,
)
from fastjsonschema.ref_resolver import RefResolver

try:
    import jsonschema
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT7
except ImportError:  # pragma: no cover - reference validator is a devel extra
    jsonschema = None


DRAFT7_SCHEMA = "http://json-schema.org/draft-07/schema"

requires_reference = pytest.mark.skipif(
    jsonschema is None,
    reason="reference jsonschema implementation is not installed",
)


def local_handlers(repo):
    """Handlers mapping backed by an in-memory dict; nothing hits the network."""
    return {"sch": repo.__getitem__}


def guarded_handlers(repo):
    """Like local_handlers but a missing URI is a definition error, never a fetch."""

    def handle(uri):
        if uri not in repo:
            raise JsonSchemaDefinitionException(f"local repository has no document {uri!r}")
        return repo[uri]

    return {"sch": handle}


def reference_validator(root, repo):
    """Build a reference validator whose registry is the same in-memory dict."""
    registry = Registry().with_resources(
        (uri, Resource.from_contents(document, default_specification=DRAFT7))
        for uri, document in repo.items()
    )
    return jsonschema.Draft7Validator(
        {"$schema": DRAFT7_SCHEMA, **root},
        registry=registry,
    )


def load_standalone(code, tmp_path, module_name):
    """Write generated code to a file and import its ``validate`` entry point."""
    (tmp_path / f"{module_name}.py").write_text(code, encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    try:
        module = importlib.import_module(module_name)
        return module.validate
    finally:
        sys.path.remove(str(tmp_path))


def function_definitions(code):
    return [line for line in code.splitlines() if line.startswith("def validate")]


# ---------------------------------------------------------------------------
# 1. Two external schemas defining a sub-object under the same name
# ---------------------------------------------------------------------------

DISTINCT_DOC_REPO = {
    # Hyphen and underscore are different characters in the URI, but the
    # generator's get_scope_name() normalises both to '_', so both documents
    # produce the same Python function identifier.
    "sch://a-b/thing": {
        "type": "object",
        "properties": {"v": {"type": "string"}},
        "required": ["v"],
    },
    "sch://a_b/thing": {
        "type": "object",
        "properties": {"v": {"type": "integer"}},
        "required": ["v"],
    },
}

DISTINCT_DOC_ROOT = {
    "type": "object",
    "properties": {
        "a": {"$ref": "sch://a-b/thing"},
        "b": {"$ref": "sch://a_b/thing"},
    },
}

DISTINCT_DOC_CASES = [
    # data, expected validity under the specification
    ({"a": {"v": "x"}, "b": {"v": 1}}, True),
    ({"a": {"v": 1}, "b": {"v": 1}}, False),
    ({"a": {"v": "x"}, "b": {"v": "x"}}, False),
]


def test_distinct_external_uris_share_one_generated_function_name():
    # The two URIs are distinct, yet they sanitise to the same identifier. This
    # is the underlying name-binding hazard the rest of this group checks.
    resolver = RefResolver.from_schema(
        {"$ref": "sch://a-b/thing"},
        handlers=local_handlers(DISTINCT_DOC_REPO),
        store={},
    )
    names = set()
    for ref in DISTINCT_DOC_REPO:
        with resolver.in_scope(ref):
            names.add(resolver.get_scope_name())
    assert names == {"validate_sch___a_b_thing"}


def test_colliding_subobjects_emit_duplicate_function_definitions():
    code = compile_to_code(DISTINCT_DOC_ROOT, handlers=local_handlers(DISTINCT_DOC_REPO))
    definitions = function_definitions(code)
    # One entry per document plus the root: two of them share a single name.
    assert len(definitions) == 3
    helper_names = {line.split("(")[0] for line in definitions if not line.startswith("def validate(")}
    assert helper_names == {"def validate_sch___a_b_thing"}


@requires_reference
@pytest.mark.parametrize("data, expected_valid", DISTINCT_DOC_CASES)
def test_reference_distinguishes_colliding_external_subobjects(data, expected_valid):
    validator = reference_validator(DISTINCT_DOC_ROOT, DISTINCT_DOC_REPO)
    assert validator.is_valid(data) is expected_valid


@requires_reference
def test_direct_compile_diverges_from_reference_on_name_collision():
    # Both positions carry a string: only the string document (a-b) should
    # accept its own field, while the integer document (a_b) must reject b.
    # Because one helper shadows the other, direct compilation accepts it.
    data = {"a": {"v": "x"}, "b": {"v": "x"}}
    validator = compile(DISTINCT_DOC_ROOT, handlers=local_handlers(DISTINCT_DOC_REPO))
    try:
        validator(copy.deepcopy(data))
    except JsonSchemaValueException:
        fast_valid = False
    else:
        fast_valid = True

    reference_valid = reference_validator(DISTINCT_DOC_ROOT, DISTINCT_DOC_REPO).is_valid(data)
    assert reference_valid is False
    # The shadowed helper makes the two external documents indistinguishable,
    # so direct compilation accepts data the reference rejects.
    assert fast_valid is not reference_valid


@requires_reference
def test_standalone_code_diverges_from_reference_the_same_way(tmp_path):
    code = compile_to_code(DISTINCT_DOC_ROOT, handlers=local_handlers(DISTINCT_DOC_REPO))
    standalone_validate = load_standalone(code, tmp_path, "colliding_subobjects")

    data = {"a": {"v": "x"}, "b": {"v": "x"}}
    try:
        standalone_validate(copy.deepcopy(data))
    except JsonSchemaValueException:
        standalone_valid = False
    else:
        standalone_valid = True

    direct_validator = compile(DISTINCT_DOC_ROOT, handlers=local_handlers(DISTINCT_DOC_REPO))
    try:
        direct_validator(copy.deepcopy(data))
    except JsonSchemaValueException:
        direct_valid = False
    else:
        direct_valid = True

    # Direct compilation and the generated file fail in the same way; both
    # disagree with the reference, i.e. this is a generator binding defect, not
    # an exec-vs-file discrepancy.
    assert standalone_valid == direct_valid is True
    assert reference_validator(DISTINCT_DOC_ROOT, DISTINCT_DOC_REPO).is_valid(data) is False


def test_collision_outcome_depends_on_compile_order():
    # Reversing property order changes which document's helper is emitted last
    # and therefore which one wins the shared name (last def shadows earlier).
    reversed_root = {
        "type": "object",
        "properties": {
            "b": {"$ref": "sch://a_b/thing"},
            "a": {"$ref": "sch://a-b/thing"},
        },
    }
    payload = {"a": {"v": 1}, "b": {"v": 1}}

    original = compile(DISTINCT_DOC_ROOT, handlers=local_handlers(DISTINCT_DOC_REPO))
    with pytest.raises(JsonSchemaValueException):
        original(copy.deepcopy(payload))

    reversed_validator = compile(reversed_root, handlers=local_handlers(DISTINCT_DOC_REPO))
    # Integer bound wins in the reversed ordering, so the same payload passes.
    assert reversed_validator(copy.deepcopy(payload)) == payload


def test_well_named_external_documents_do_not_collide():
    # Contrast case: different base URIs with an identically named sub-path get
    # distinct helper names and validate correctly. This bounds the defect to
    # the identifier sanitisation rather than to same-named sub-objects alone.
    repo = {
        "sch://alpha": {
            "definitions": {"thing": {"type": "object", "properties": {"v": {"type": "string"}}}},
        },
        "sch://beta": {
            "definitions": {"thing": {"type": "object", "properties": {"v": {"type": "integer"}}}},
        },
    }
    root = {
        "type": "object",
        "properties": {
            "a": {"$ref": "sch://alpha#/definitions/thing"},
            "b": {"$ref": "sch://beta#/definitions/thing"},
        },
    }
    code = compile_to_code(root, handlers=local_handlers(repo))
    helper_names = {
        line.split("(")[0]
        for line in function_definitions(code)
        if not line.startswith("def validate(")
    }
    assert helper_names == {
        "def validate_sch___alpha__definitions_thing",
        "def validate_sch___beta__definitions_thing",
    }

    validator = compile(root, handlers=local_handlers(repo))
    assert validator({"a": {"v": "x"}, "b": {"v": 1}}) == {"a": {"v": "x"}, "b": {"v": 1}}
    with pytest.raises(JsonSchemaValueException) as exc:
        validator({"a": {"v": 1}, "b": {"v": 1}})
    assert exc.value.name == "data.a.v"


# ---------------------------------------------------------------------------
# 2. The same reference reused from different positions
# ---------------------------------------------------------------------------

SHARED_REPO = {
    "sch://shared": {
        "type": "object",
        "properties": {"v": {"type": "string"}},
        "required": ["v"],
    },
}

REUSED_ROOT = {
    "type": "object",
    "properties": {
        "first": {"$ref": "sch://shared"},
        "second": {"$ref": "sch://shared"},
        "nested": {"type": "array", "items": {"$ref": "sch://shared"}},
    },
}

REUSED_VALID = {
    "first": {"v": "a"},
    "second": {"v": "b"},
    "nested": [{"v": "c"}],
}


def test_reused_reference_generates_one_helper_for_all_positions():
    code = compile_to_code(REUSED_ROOT, handlers=local_handlers(SHARED_REPO))
    assert function_definitions(code) == [
        "def validate(data, custom_formats={}, name_prefix=None):",
        "def validate_sch___shared(data, custom_formats={}, name_prefix=None):",
    ]


@pytest.mark.parametrize(
    "payload, path",
    [
        ({"first": {"v": 1}}, "data.first.v"),
        ({"second": {"v": 1}}, "data.second.v"),
        ({"nested": [{"v": 1}]}, "data.nested[0].v"),
    ],
)
def test_reused_reference_reports_each_position_separately(payload, path):
    validator = compile(REUSED_ROOT, handlers=local_handlers(SHARED_REPO))
    with pytest.raises(JsonSchemaValueException) as exc:
        validator(copy.deepcopy(payload))
    assert exc.value.name == path
    assert exc.value.message == f"{path} must be string"


def test_reused_reference_standalone_matches_direct_compile(tmp_path):
    direct = compile(REUSED_ROOT, handlers=local_handlers(SHARED_REPO))
    code = compile_to_code(REUSED_ROOT, handlers=local_handlers(SHARED_REPO))
    standalone = load_standalone(code, tmp_path, "reused_reference")

    assert standalone(copy.deepcopy(REUSED_VALID)) == REUSED_VALID
    assert direct(copy.deepcopy(REUSED_VALID)) == REUSED_VALID
    with pytest.raises(JsonSchemaValueException) as exc:
        standalone({"nested": [{"v": 2}]})
    assert exc.value.name == "data.nested[0].v"


@requires_reference
def test_reused_reference_matches_reference_validator():
    validator = reference_validator(REUSED_ROOT, SHARED_REPO)
    assert validator.is_valid(REUSED_VALID) is True
    assert validator.is_valid({"nested": [{"v": 1}]}) is False


def test_same_document_via_different_uri_spellings_emits_duplicate_helpers():
    # 'sch://shared' and 'sch://shared#/' address the same document, but the
    # resolver keeps them under different URI keys while get_scope_name()
    # reduces both to one identifier. Both helpers validate the *same* schema,
    # so this duplicate definition is harmless redundancy rather than a binding
    # mistake: the specification fixes validation results, not the names or
    # number of generated helper functions.
    root = {
        "type": "object",
        "properties": {
            "first": {"$ref": "sch://shared"},
            "second": {"$ref": "sch://shared#/"},
        },
    }
    code = compile_to_code(root, handlers=local_handlers(SHARED_REPO))
    helper_lines = [
        line
        for line in function_definitions(code)
        if not line.startswith("def validate(")
    ]
    assert len(helper_lines) == 2
    assert {line.split("(")[0] for line in helper_lines} == {
        "def validate_sch___shared"
    }

    validator = compile(root, handlers=local_handlers(SHARED_REPO))
    assert validator({"first": {"v": "a"}, "second": {"v": "b"}}) == {
        "first": {"v": "a"},
        "second": {"v": "b"},
    }
    with pytest.raises(JsonSchemaValueException) as exc:
        validator({"first": {"v": 1}, "second": {"v": "b"}})
    assert exc.value.name == "data.first.v"


# ---------------------------------------------------------------------------
# 3. One root compiled successively with different options
# ---------------------------------------------------------------------------

DEFAULT_ROOT = {
    "type": "object",
    "properties": {"a": {"type": "number", "default": 42}},
}


def test_successive_compiles_keep_their_own_default_option():
    with_default = compile(DEFAULT_ROOT, use_default=True)
    without_default = compile(DEFAULT_ROOT, use_default=False)

    assert with_default({}) == {"a": 42}
    assert without_default({}) == {}
    # Reusing the earlier validator after a later compile must not be affected.
    assert with_default({}) == {"a": 42}
    assert without_default({}) == {}


def test_default_option_changes_generated_code_text():
    with_default_code = compile_to_code(DEFAULT_ROOT, use_default=True)
    without_default_code = compile_to_code(DEFAULT_ROOT, use_default=False)
    assert 'else: data["a"] = 42' in with_default_code
    assert 'else: data["a"] = 42' not in without_default_code


def test_successive_compiles_keep_detailed_and_fast_fail_options():
    terse = compile(DEFAULT_ROOT, detailed_exceptions=False)
    collecting = compile(DEFAULT_ROOT, fast_fail=False)
    detailed = compile(DEFAULT_ROOT)

    with pytest.raises(JsonSchemaValueException) as terse_exc:
        terse({"a": "bad"})
    assert terse_exc.value.rule is None
    assert terse_exc.value.message == "data.a must be number"

    with pytest.raises(JsonSchemaValuesException) as collected_exc:
        collecting({"a": "bad"})
    assert len(collected_exc.value.errors) == 1

    with pytest.raises(JsonSchemaValueException) as detailed_exc:
        detailed({"a": "bad"})
    assert detailed_exc.value.rule == "type"


def test_recompiling_same_root_with_remote_ref_is_idempotent():
    root = {
        "type": "object",
        "properties": {"a": {"$ref": "sch://shared"}},
    }

    compile(root, handlers=local_handlers(SHARED_REPO))
    after_first = copy.deepcopy(root)
    compile(root, handlers=local_handlers(SHARED_REPO))
    after_second = copy.deepcopy(root)

    # The resolver rewrites $ref in place to an absolute URI, but the rewrite
    # is idempotent: later compiles of the same dict must not keep mutating it.
    assert after_first == after_second
    assert root["properties"]["a"]["$ref"] == "sch://shared"

    validator = compile(root, handlers=local_handlers(SHARED_REPO))
    assert validator({"a": {"v": "ok"}}) == {"a": {"v": "ok"}}


def test_local_ref_rewrite_survives_repeated_compiles():
    root = {
        "type": "object",
        "properties": {"a": {"$ref": "#/definitions/s"}},
        "definitions": {"s": {"type": "string"}},
    }
    for _ in range(3):
        validator = compile(root)
    assert root["properties"]["a"]["$ref"] == "#/definitions/s"
    assert validator({"a": "ok"}) == {"a": "ok"}
    with pytest.raises(JsonSchemaValueException) as exc:
        validator({"a": 1})
    assert exc.value.name == "data.a"


def test_separate_compiles_do_not_share_remote_cache():
    # Each compile builds a brand-new resolver store, so the same URI can back
    # different documents across compilations without cross-contamination.
    string_repo = {"sch://schema": {"type": "string"}}
    integer_repo = {"sch://schema": {"type": "integer"}}

    string_validator = compile(
        {"$ref": "sch://schema"}, handlers=local_handlers(string_repo)
    )
    integer_validator = compile(
        {"$ref": "sch://schema"}, handlers=local_handlers(integer_repo)
    )

    assert string_validator("hello") == "hello"
    assert integer_validator(5) == 5
    with pytest.raises(JsonSchemaValueException):
        integer_validator("hello")
    # The first validator keeps its own binding after the second compile.
    assert string_validator("still a string") == "still a string"


def test_unresolvable_local_dict_reference_raises_definition_exception():
    # Guard ensuring references really are resolved locally: a missing entry in
    # the backing dict must surface as a definition error, never as a fetch.
    with pytest.raises(JsonSchemaDefinitionException):
        compile({"$ref": "sch://missing"}, handlers=guarded_handlers(SHARED_REPO))
