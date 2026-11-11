"""
Schema migration preview.

Interpret the same YAML source under an old and a new schema without
modifying any existing YAML object or file.  The preview reports:

* Type changes      - the same source position now resolves to a different
                      Python type (e.g. the text "yes" becoming ``True``).
* Default changes   - positions missing from the source text whose
                      effective default differs between the schemas.
* Rejected positions - positions the new schema refuses to accept.

Configurations that cannot be loaded are reported separately from
configurations which load but whose meaning changes.

Nothing in this module mutates the supplied YAML text.  Applying the new
interpretation (``MigrationPreview.apply``) re-parses the original text and
only happens when the caller explicitly asks for it.
"""
from dataclasses import dataclass, field
from typing import Any as TAny
from typing import List, Optional, Tuple

from strictyaml.parser import load
from strictyaml.any_validator import Any
from strictyaml.compound import (
    FixedSeq,
    Map,
    MapCombined,
    MapPattern,
    Seq,
    UniqueSeq,
)
from strictyaml.exceptions import YAMLValidationError
from strictyaml.ruamel import YAMLError
from strictyaml.validators import OrValidator, Validator
from strictyaml.yamllocation import YAMLChunk

Path = Tuple[TAny, ...]


class _MissingDefault(object):
    """Sentinel marking that a schema supplies no default for a key."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(_MissingDefault, cls).__new__(cls)
        return cls._instance

    def __repr__(self):
        return "<no default>"

    def __bool__(self):
        return False


MISSING = _MissingDefault()


class MigrationApplyError(Exception):
    """Raised when a preview which found problems is asked to apply itself."""


@dataclass
class TypeChange(object):
    """A source position whose parsed value type changed between schemas."""

    path: Path
    old_value: TAny
    old_type: str
    new_value: TAny
    new_type: str
    line: Optional[int]
    column: Optional[int] = None

    @property
    def path_string(self):
        return _format_path(self.path)


@dataclass
class DefaultChange(object):
    """A position absent from the source whose effective default changed."""

    path: Path
    old_default: TAny
    new_default: TAny
    old_type: Optional[str]
    new_type: Optional[str]
    line: Optional[int]
    column: Optional[int] = None

    @property
    def path_string(self):
        return _format_path(self.path)

    @property
    def old_default_present(self):
        return not isinstance(self.old_default, _MissingDefault)

    @property
    def new_default_present(self):
        return not isinstance(self.new_default, _MissingDefault)


@dataclass
class RejectedPosition(object):
    """A source position the new schema refuses to validate."""

    path: Path
    message: str
    line: Optional[int]
    column: Optional[int] = None

    @property
    def path_string(self):
        return _format_path(self.path)


@dataclass
class LoadFailure(object):
    """A whole-document load failure, kept apart from semantic changes."""

    schema: str
    error_type: str
    message: str
    line: Optional[int]
    column: Optional[int] = None


@dataclass
class MigrationPreview(object):
    """
    Result of interpreting one YAML document under two schemas.

    ``loadable`` is False only when the old schema cannot load the document.
    Semantic changes (type/default changes) are only populated for documents
    the old schema can load; ``rejected`` lists positions the new schema
    does not accept.
    """

    label: str
    type_changes: List[TypeChange] = field(default_factory=list)
    default_changes: List[DefaultChange] = field(default_factory=list)
    rejected: List[RejectedPosition] = field(default_factory=list)
    old_load_error: Optional[LoadFailure] = None
    new_load_error: Optional[LoadFailure] = None
    _yaml_string: Optional[str] = None
    _new_schema: Optional[Validator] = None

    @property
    def loadable(self):
        """True when the old schema (and the YAML syntax) accept the document."""
        return self.old_load_error is None

    @property
    def semantic_changes(self):
        """All positions which load but change meaning."""
        return list(self.type_changes) + list(self.default_changes)

    @property
    def compatible_with_new_schema(self):
        """True when the new schema accepts the whole document."""
        return self.new_load_error is None and not self.rejected

    @property
    def has_changes(self):
        return bool(
            self.type_changes
            or self.default_changes
            or self.rejected
            or self.old_load_error
            or self.new_load_error
        )

    def apply(self):
        """
        Parse the original YAML text again using the new schema.

        The preview never mutates an existing YAML object or file, so
        applying means a fresh, independent parse of the original text.
        Refused when the new schema does not accept the document.
        """
        if self.old_load_error is not None:
            raise MigrationApplyError(
                "Cannot apply new schema: the document does not load under "
                "the old schema either."
            )
        if not self.compatible_with_new_schema:
            raise MigrationApplyError(
                "Cannot apply new schema: {0} position(s) were rejected. "
                "Fix the document or the schema before applying.".format(
                    len(self.rejected)
                )
            )
        return load(self._yaml_string, self._new_schema, label=self.label)

    def as_dict(self):
        """Plain dict representation, suitable for JSON serialization."""
        return {
            "loadable": self.loadable,
            "compatible_with_new_schema": self.compatible_with_new_schema,
            "type_changes": [
                {
                    "path": list(change.path),
                    "line": change.line,
                    "column": change.column,
                    "old_value": change.old_value,
                    "old_type": change.old_type,
                    "new_value": change.new_value,
                    "new_type": change.new_type,
                }
                for change in self.type_changes
            ],
            "default_changes": [
                {
                    "path": list(change.path),
                    "line": change.line,
                    "column": change.column,
                    "old_default_present": change.old_default_present,
                    "old_default": _jsonable(change.old_default),
                    "old_type": change.old_type,
                    "new_default_present": change.new_default_present,
                    "new_default": _jsonable(change.new_default),
                    "new_type": change.new_type,
                }
                for change in self.default_changes
            ],
            "rejected": [
                {
                    "path": list(position.path),
                    "line": position.line,
                    "column": position.column,
                    "message": position.message,
                }
                for position in self.rejected
            ],
            "old_load_error": _failure_dict(self.old_load_error),
            "new_load_error": _failure_dict(self.new_load_error),
        }

    def describe(self):
        """Human readable summary of the preview."""
        lines = []
        if self.old_load_error is not None:
            lines.append("Document could not be loaded with the old schema:")
            lines.append("  {0}".format(_indent(self.old_load_error.message)))
        if self.new_load_error is not None and self.old_load_error is None:
            lines.append("Document could not be loaded with the new schema:")
            lines.append("  {0}".format(_indent(self.new_load_error.message)))

        if self.type_changes:
            lines.append("Type changes:")
            for change in self.type_changes:
                lines.append(
                    "  {0} (line {1}): {2!r} ({3}) -> {4!r} ({5})".format(
                        change.path_string,
                        _line_text(change.line),
                        change.old_value,
                        change.old_type,
                        change.new_value,
                        change.new_type,
                    )
                )

        if self.default_changes:
            lines.append("Default changes:")
            for change in self.default_changes:
                lines.append(
                    "  {0} (line {1}): {2!r} -> {3!r}".format(
                        change.path_string,
                        _line_text(change.line),
                        change.old_default,
                        change.new_default,
                    )
                )

        if self.rejected:
            lines.append("Rejected by the new schema:")
            for position in self.rejected:
                lines.append(
                    "  {0} (line {1}): {2}".format(
                        position.path_string,
                        _line_text(position.line),
                        _indent(position.message),
                    )
                )

        if not lines:
            lines.append("No differences between the old and new interpretations.")
        return "\n".join(lines)

    def __str__(self):
        return self.describe()


def preview_migration(yaml_string, old_schema, new_schema, label=None):
    """
    Interpret ``yaml_string`` under ``old_schema`` and ``new_schema`` and
    return a :class:`MigrationPreview` describing the differences.

    The YAML text is parsed independently for each schema, so no existing
    YAML object or file is touched.  Nothing is applied until the caller
    invokes :meth:`MigrationPreview.apply`.
    """
    if label is None:
        label = "<unicode string>"

    preview = MigrationPreview(label=label)
    preview._yaml_string = yaml_string
    preview._new_schema = new_schema

    try:
        source = load(yaml_string, label=label)
    except YAMLError as error:
        preview.old_load_error = _load_failure("old", error)
        return preview

    try:
        old_document = load(yaml_string, old_schema, label=label)
    except YAMLError as error:
        preview.old_load_error = _load_failure("old", error)
        return preview

    try:
        new_document = load(yaml_string, new_schema, label=label)
    except YAMLError as error:
        preview.new_load_error = _load_failure("new", error)
        new_document = None

    source_paths = _collect_paths(source)
    _walk_for_changes(preview, old_document, new_schema, label, source_paths)

    if new_document is not None:
        _diff_defaults(preview, source, old_document, new_document, source_paths)
    elif not preview.rejected:
        preview.rejected.append(
            RejectedPosition(
                path=(),
                message=preview.new_load_error.message,
                line=preview.new_load_error.line,
                column=preview.new_load_error.column,
            )
        )

    return preview


def _walk_for_changes(
    preview, old_node, new_validator, label, source_paths, path=()
):
    """
    Walk the old document structure, validating each node against the new
    schema in isolation so that one bad leaf never hides the others.
    """
    new_node, error = _try_validate(old_node, new_validator, label)

    if error is not None:
        if old_node.is_scalar() or path not in source_paths:
            if path in source_paths:
                line, column = _mark_line_column(error, old_node)
                preview.rejected.append(
                    RejectedPosition(
                        path=path,
                        message=str(error).strip(),
                        line=line,
                        column=column,
                    )
                )
            return
        selected = _structural_validator(new_validator)
        if selected is None:
            line, column = _mark_line_column(error, old_node)
            preview.rejected.append(
                RejectedPosition(
                    path=path,
                    message=str(error).strip(),
                    line=line,
                    column=column,
                )
            )
            return
    else:
        if old_node.is_scalar():
            if path in source_paths:
                old_value = old_node.data
                new_value = new_node.data
                if type(old_value) is not type(new_value):
                    line, column = _node_line_column(old_node)
                    preview.type_changes.append(
                        TypeChange(
                            path=path,
                            old_value=old_value,
                            old_type=type(old_value).__name__,
                            new_value=new_value,
                            new_type=type(new_value).__name__,
                            line=line,
                            column=column,
                        )
                    )
            return
        selected = _structural_validator(
            new_node._selected_validator
            if new_node._selected_validator is not None
            else new_validator
        )
        if selected is None:
            return

    if old_node.is_mapping():
        _walk_mapping(
            preview, old_node, selected, label, source_paths, path
        )
    else:
        _walk_sequence(
            preview, old_node, selected, label, source_paths, path
        )


def _walk_mapping(preview, old_node, selected, label, source_paths, path):
    for key_yaml, old_child in old_node.items():
        child_path = path + (key_yaml.data,)
        child_validator = _mapping_child_validator(selected, key_yaml.data)
        if child_validator is None:
            if child_path in source_paths:
                line, column = _node_line_column(old_child)
                preview.rejected.append(
                    RejectedPosition(
                        path=child_path,
                        message="unexpected key not in schema '{0}'".format(
                            key_yaml.data
                        ),
                        line=line,
                        column=column,
                    )
                )
            continue
        _walk_for_changes(
            preview,
            old_child,
            child_validator,
            label,
            source_paths,
            child_path,
        )


def _walk_sequence(preview, old_node, selected, label, source_paths, path):
    for index, old_child in enumerate(old_node):
        child_path = path + (index,)
        child_validator = _sequence_child_validator(selected, index)
        if child_validator is None:
            if child_path in source_paths:
                line, column = _node_line_column(old_child)
                preview.rejected.append(
                    RejectedPosition(
                        path=child_path,
                        message="sequence item has no matching schema validator",
                        line=line,
                        column=column,
                    )
                )
            continue
        _walk_for_changes(
            preview,
            old_child,
            child_validator,
            label,
            source_paths,
            child_path,
        )


def _try_validate(old_node, validator, label):
    """Validate an isolated copy of one node; return (node, error)."""
    isolated = YAMLChunk(
        old_node._chunk._ruamelparsed,
        pointer=old_node._chunk.pointer,
        label=label,
        key_association=dict(old_node._chunk.key_association),
    )
    try:
        return validator(isolated), None
    except YAMLValidationError as error:
        return None, error


def _structural_validator(validator):
    """
    Reduce a validator to one describing mapping/sequence structure, or
    ``None`` when the schema cannot be descended into at this point.
    """
    if isinstance(validator, OrValidator):
        branch_a = _structural_validator(validator._validator_a)
        if branch_a is not None:
            return branch_a
        return _structural_validator(validator._validator_b)
    if isinstance(validator, Any):
        return validator
    if isinstance(
        validator, (Map, MapCombined, MapPattern, Seq, UniqueSeq, FixedSeq)
    ):
        return validator
    return None


def _mapping_child_validator(selected, key):
    if isinstance(selected, (Map, MapCombined)):
        try:
            return selected.get_validator(key)
        except KeyError:
            return None
    if isinstance(selected, MapPattern):
        return selected._value_validator
    if isinstance(selected, Any):
        return Any()
    return None


def _sequence_child_validator(selected, index):
    if isinstance(selected, (Seq, UniqueSeq, Any)):
        return selected if isinstance(selected, Any) else selected._validator
    if isinstance(selected, FixedSeq):
        if index < len(selected._validators):
            return selected._validators[index]
        return None
    return None


def _diff_defaults(preview, source, old_document, new_document, source_paths):
    """Compare effective defaults at positions absent from the source text."""
    old_data = old_document.data
    new_data = new_document.data

    def recurse(path, old_value, new_value):
        if isinstance(new_value, dict) or isinstance(old_value, dict):
            old_map = old_value if isinstance(old_value, dict) else {}
            new_map = new_value if isinstance(new_value, dict) else {}
            for key in sorted(set(old_map) | set(new_map), key=str):
                recurse(path + (key,), old_map.get(key, MISSING), new_map.get(key, MISSING))
            return
        if isinstance(new_value, list) or isinstance(old_value, list):
            old_list = old_value if isinstance(old_value, list) else []
            new_list = new_value if isinstance(new_value, list) else []
            for index in range(max(len(old_list), len(new_list))):
                recurse(
                    path + (index,),
                    old_list[index] if index < len(old_list) else MISSING,
                    new_list[index] if index < len(new_list) else MISSING,
                )
            return
        if path in source_paths:
            return
        if old_value is MISSING and new_value is MISSING:
            return
        if old_value == new_value and type(old_value) is type(new_value):
            return
        parent = _navigate(old_document, path[:-1])
        if parent is None:
            parent = _navigate(new_document, path[:-1])
        line, column = _node_line_column(parent) if parent is not None else (None, None)
        preview.default_changes.append(
            DefaultChange(
                path=path,
                old_default=old_value,
                new_default=new_value,
                old_type=None if old_value is MISSING else type(old_value).__name__,
                new_type=None if new_value is MISSING else type(new_value).__name__,
                line=line,
                column=column,
            )
        )

    recurse((), old_data, new_data)


def _collect_paths(node, path=()):
    paths = {path}
    if node.is_mapping():
        for key, child in node.items():
            paths.update(_collect_paths(child, path + (key.data,)))
    elif node.is_sequence():
        for index, child in enumerate(node):
            paths.update(_collect_paths(child, path + (index,)))
    return paths


def _navigate(node, path):
    current = node
    for part in path:
        try:
            current = current[part]
        except (KeyError, IndexError, TypeError):
            return None
    return current


def _node_line_column(node):
    if node is None:
        return None, None
    try:
        line = node.start_line
    except Exception:
        line = None
    return line, None


def _mark_line_column(error, node):
    mark = getattr(error, "problem_mark", None)
    if mark is not None:
        return mark.line + 1, mark.column
    return _node_line_column(node)


def _load_failure(schema, error):
    mark = getattr(error, "problem_mark", None)
    line = mark.line + 1 if mark is not None else None
    column = mark.column if mark is not None else None
    return LoadFailure(
        schema=schema,
        error_type=type(error).__name__,
        message=str(error).strip(),
        line=line,
        column=column,
    )


def _failure_dict(failure):
    if failure is None:
        return None
    return {
        "schema": failure.schema,
        "error_type": failure.error_type,
        "message": failure.message,
        "line": failure.line,
        "column": failure.column,
    }


def _format_path(path):
    if not path:
        return "<root>"
    parts = []
    for part in path:
        if isinstance(part, int):
            parts.append("[{0}]".format(part))
        else:
            parts.append(str(part))
    return ".".join(parts)


def _jsonable(value):
    if isinstance(value, _MissingDefault):
        return None
    return value


def _line_text(line):
    return "?" if line is None else str(line)


def _indent(message):
    return message.replace("\n", "\n  ")
