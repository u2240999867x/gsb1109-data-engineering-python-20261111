"""
Parsing code for YAML streams containing more than one document.
"""
from strictyaml import ruamel as ruamelyaml
from strictyaml import exceptions
from strictyaml import utils
from strictyaml.any_validator import Any
from strictyaml.parser import StrictYAMLLoader
from strictyaml.validators import Validator
from strictyaml.yamllocation import YAMLChunk
from strictyaml.ruamel.comments import CommentedSeq, CommentedMap


def _document_label(label, document_index):
    return "{0} (document {1})".format(label, document_index + 1)


class YAMLDocument(object):
    """
    A single document parsed out of a multi-document YAML stream.

    The line numbers refer to the position of the document in the
    original, un-split YAML stream rather than in the document itself.
    """

    def __init__(self, yaml, document_index, label, start_line, end_line):
        self._yaml = yaml
        self._document_index = document_index
        self._label = label
        self._start_line = start_line
        self._end_line = end_line

    @property
    def yaml(self):
        return self._yaml

    @property
    def index(self):
        return self._document_index

    @property
    def label(self):
        return self._label

    @property
    def start_line(self):
        return self._start_line

    @property
    def end_line(self):
        return self._end_line

    def __repr__(self):
        return "<YAMLDocument {0} from {1}>".format(
            self._document_index + 1, self._label
        )


class DocumentFailure(object):
    """
    A document which could not be parsed or validated when
    continue_on_errors was enabled.
    """

    def __init__(self, document_index, label, error):
        self._document_index = document_index
        self._label = label
        self._error = error

    @property
    def index(self):
        return self._document_index

    @property
    def label(self):
        return self._label

    @property
    def error(self):
        return self._error

    def __repr__(self):
        return "<DocumentFailure {0} from {1}: {2}>".format(
            self._document_index + 1, self._label, type(self._error).__name__
        )


class YAMLStream(object):
    """
    The result of reading a multi-document YAML stream.
    """

    def __init__(self, label, documents, failures):
        self._label = label
        self._documents = documents
        self._failures = failures

    @property
    def label(self):
        return self._label

    @property
    def documents(self):
        return self._documents

    @property
    def failures(self):
        return self._failures

    @property
    def is_valid(self):
        return len(self._failures) == 0

    def __iter__(self):
        return iter(self._documents)

    def __len__(self):
        return len(self._documents)

    def __getitem__(self, position):
        return self._documents[position]

    def __repr__(self):
        return "<YAMLStream from {0}: {1} document(s), {2} failure(s)>".format(
            self._label, len(self._documents), len(self._failures)
        )


def _normalize_schema_argument(schemas):
    if schemas is None:
        return None
    if isinstance(schemas, Validator):
        return schemas
    if not isinstance(schemas, (list, tuple)):
        raise exceptions.MultiDocumentError(
            "schemas must be a Validator, a list/tuple of Validators "
            "(or None), or None."
        )
    for schema in schemas:
        if schema is not None and not isinstance(schema, Validator):
            raise TypeError(
                "Each schema must be a Validator or None (for no schema), "
                "found {0}.".format(type(schema).__name__)
            )
    return list(schemas)


def _normalize_label_argument(labels):
    if labels is None:
        return None
    if not isinstance(labels, (list, tuple)):
        raise exceptions.MultiDocumentError(
            "labels must be a list/tuple of strings or None."
        )
    for document_label in labels:
        if not utils.is_string(document_label):
            raise TypeError(
                "Each label must be a string, found {0}.".format(
                    type(document_label).__name__
                )
            )
    return list(labels)


def _schema_for(schemas, document_index):
    if schemas is None:
        return Any()
    if isinstance(schemas, Validator):
        return schemas
    if document_index < len(schemas) and schemas[document_index] is not None:
        return schemas[document_index]
    return Any()


def _relabel_error(error, document_index, document_label):
    error.document_index = document_index
    error.document_label = document_label
    for mark in (
        getattr(error, "context_mark", None),
        getattr(error, "problem_mark", None),
    ):
        if mark is not None:
            mark.name = document_label


def load_all(
    yaml_string,
    schemas=None,
    label="<unicode string>",
    labels=None,
    allow_flow_style=False,
    continue_on_errors=False,
):
    """
    Parse every YAML document in a string (documents separated by '---')
    and validate each one against its own schema.

    schemas is either:
      * None - no document is validated against a schema (Any() is used),
      * a single Validator - applied to every document, or
      * a list/tuple with one Validator (or None) per document, indexed by
        document number.

    labels is an optional list/tuple with one label per document. When not
    supplied, documents are labeled '<label> (document N)'.

    Errors carry the document number and the label of the document they
    occurred in, while line/column numbers keep referring to the original
    YAML stream.

    If continue_on_errors is False (the default) the first failure aborts
    reading and the original error is raised. If True, parsing and validation
    keep going and failures are collected in stream.failures. Errors raised
    while scanning/parsing (e.g. malformed YAML or disallowed tokens) corrupt
    the stream state and therefore always abort reading.
    """
    if not utils.is_string(yaml_string):
        raise TypeError("StrictYAML can only read a string of valid YAML.")

    schemas = _normalize_schema_argument(schemas)
    labels = _normalize_label_argument(labels)

    dynamic_loader = type(
        "DynamicStrictYAMLLoader",
        (StrictYAMLLoader,),
        {"label": label, "allow_flow_style": allow_flow_style},
    )
    loader = dynamic_loader(yaml_string)

    documents = []
    failures = []
    document_index = 0
    stream_aborted = False

    try:
        while True:
            if labels is not None and document_index < len(labels):
                document_label = labels[document_index]
            else:
                document_label = _document_label(label, document_index)

            # Composing a node drives the scanner/parser. A failure here
            # (malformed syntax, disallowed token, etc.) leaves the stream
            # in an unknown state, so reading cannot continue.
            try:
                has_document = loader._constructor.check_data()
                if has_document:
                    node = loader._composer.get_node()
            except ruamelyaml.YAMLError as compose_error:
                _relabel_error(compose_error, document_index, document_label)
                stream_aborted = True
                if not continue_on_errors:
                    raise compose_error
                failures.append(
                    DocumentFailure(document_index, document_label, compose_error)
                )
                break

            if not has_document:
                break

            # Construction (e.g. duplicate key detection) happens on a node
            # that was fully composed, so a failure here is recoverable and
            # the next document can still be read.
            try:
                document = loader._constructor.construct_document(node)
            except ruamelyaml.YAMLError as construct_error:
                _relabel_error(construct_error, document_index, document_label)
                if not continue_on_errors:
                    raise construct_error
                failures.append(
                    DocumentFailure(document_index, document_label, construct_error)
                )
                document_index += 1
                continue

            start_line = node.start_mark.line + 1
            end_line = node.end_mark.line + 1

            if not isinstance(document, (CommentedMap, CommentedSeq)):
                document = yaml_string[
                    node.start_mark.index : node.end_mark.index
                ]

            schema = _schema_for(schemas, document_index)
            chunk = YAMLChunk(
                document,
                label=document_label,
                line_offset=node.start_mark.line,
            )

            try:
                parsed = schema(chunk)
            except exceptions.YAMLValidationError as validation_error:
                validation_error.document_index = document_index
                validation_error.document_label = document_label
                if not continue_on_errors:
                    raise validation_error
                failures.append(
                    DocumentFailure(document_index, document_label, validation_error)
                )
            else:
                documents.append(
                    YAMLDocument(
                        parsed,
                        document_index,
                        document_label,
                        start_line,
                        end_line,
                    )
                )

            document_index += 1
    finally:
        loader._parser.dispose()
        try:
            loader._reader.reset_reader()
        except AttributeError:
            pass
        try:
            loader._scanner.reset_scanner()
        except AttributeError:
            pass

    # Only verify schema/label counts when the whole stream was read, since
    # an aborted stream may legitimately contain fewer documents.
    if not stream_aborted:
        if isinstance(schemas, list) and len(schemas) != document_index:
            raise exceptions.MultiDocumentError(
                "{0} schema(s) were supplied for a stream containing "
                "{1} document(s). When passing a list of schemas there must "
                "be exactly one (Validator or None) per document.".format(
                    len(schemas), document_index
                )
            )
        if labels is not None and len(labels) != document_index:
            raise exceptions.MultiDocumentError(
                "{0} label(s) were supplied for a stream containing "
                "{1} document(s). When passing labels there must be exactly "
                "one per document.".format(len(labels), document_index)
            )

    return YAMLStream(label, documents, failures)
