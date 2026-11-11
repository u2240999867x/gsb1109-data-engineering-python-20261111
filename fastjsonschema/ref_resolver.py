# pylint: disable=import-outside-toplevel

"""
JSON Schema URI resolution scopes and dereferencing

https://tools.ietf.org/id/draft-zyp-json-schema-04.html#rfc.section.7

Code adapted from https://github.com/Julian/jsonschema
"""

import contextlib
import json
import re
import sys
from collections.abc import Callable, Iterator, Mapping
from typing import Any
from urllib import parse as urlparse
from urllib.parse import unquote

from .exceptions import (
    JsonSchemaDefinitionException,
    RefContentConflict,
    RefFetchError,
    RefPolicyDenied,
    RefRetrievalException,
)
from .ref_policy import (
    RefPolicy,
    RefRetrievalRecord,
    content_fingerprint,
    default_http_fetch,
    handler_fetch,
    resource_id_of,
)

MAX_SCHEMA_WALK_DEPTH = min(500, sys.getrecursionlimit() // 2)

_STATUS_BY_EXCEPTION = {
    RefPolicyDenied: 'denied',
    RefFetchError: 'failed',
    RefContentConflict: 'conflict',
}


def get_id(schema: dict[str, Any]) -> str:
    """
    Originally ID was `id` and since v7 it's `$id`.
    """
    return schema.get('$id', schema.get('id', ''))


def resolve_path(schema: Any, fragment: str) -> Any:
    """
    Return definition from path.

    Path is unescaped according https://tools.ietf.org/html/rfc6901
    """
    fragment = fragment.lstrip('/')
    parts = unquote(fragment).split('/') if fragment else []
    for part in parts:
        part = part.replace('~1', '/').replace('~0', '~')
        if isinstance(schema, list):
            schema = schema[int(part)]
        elif part in schema:
            schema = schema[part]
        else:
            raise JsonSchemaDefinitionException('Unresolvable ref: {}'.format(part))
    return schema


def normalize(uri: str) -> str:
    return urlparse.urlsplit(uri).geturl()


def resolve_remote(uri: str, handlers: Mapping[str, Callable[[str], Any]]) -> Any:
    """
    Resolve a remote ``uri``.

    .. note::

        urllib library is used to fetch requests from the remote ``uri``
        if handlers does notdefine otherwise.
    """
    scheme = urlparse.urlsplit(uri).scheme
    if scheme in handlers:
        result = handlers[scheme](uri)
    else:
        from urllib.request import urlopen

        with urlopen(uri) as response:
            encoding = response.info().get_content_charset() or 'utf-8'
            try:
                result = json.loads(response.read().decode(encoding),)
            except ValueError as exc:
                raise JsonSchemaDefinitionException('{} failed to decode'.format(uri)) from exc
    return result


class RefResolver:
    """
    Resolve JSON References.
    """

    # pylint: disable=dangerous-default-value,too-many-arguments
    def __init__(
        self,
        base_uri: str,
        schema: dict[str, Any] | bool,
        store: dict[str, Any] = {},
        cache: bool = True,
        handlers: Mapping[str, Callable[[str], Any]] = {},
        ref_policy: RefPolicy | None = None,
        retrieval_log: list[RefRetrievalRecord] | None = None,
    ) -> None:
        """
        `base_uri` is URI of the referring document from the `schema`.
        `store` is an dictionary that will be used to cache the fetched schemas
        (if `cache=True`).

        `ref_policy` is an optional call-level :any:`RefPolicy` controlling
        retrieval of remote references (allowed targets, redirects, response
        size and resource identity). When omitted, the legacy behavior is
        kept: custom `handlers` are used when registered for the URI scheme
        and anything else is fetched with urllib, without restrictions.

        `retrieval_log` is an optional list collecting
        :any:`RefRetrievalRecord` provenance entries for every remote
        reference retrieval attempt. When omitted, a fresh list is created
        and exposed as ``retrieval_log``.

        Please notice that you can have caching problems when compiling schemas
        with colliding `$ref`. To force overwriting use `cache=False` or
        explicitly pass the `store` argument (with a brand new dictionary)
        """
        self.base_uri = base_uri
        self.resolution_scope = base_uri
        self.schema = schema
        self.store = store
        self.cache = cache
        self.handlers = handlers
        self.ref_policy = ref_policy
        self.retrieval_log: list[RefRetrievalRecord] = retrieval_log if retrieval_log is not None else []
        self._walked_uris: set[str] = set()
        self._retrieved: dict[str, str] = {}
        self.walk(schema)
        self._walked_uris.add(normalize(base_uri) if base_uri else '')

    @classmethod
    def from_schema(
        cls,
        schema: dict[str, Any] | bool,
        handlers: Mapping[str, Callable[[str], Any]] = {},
        ref_policy: RefPolicy | None = None,
        **kwargs: Any,
    ) -> 'RefResolver':
        """
        Construct a resolver from a JSON schema object.
        """
        return cls(
            get_id(schema) if isinstance(schema, dict) else '',
            schema,
            handlers=handlers,
            ref_policy=ref_policy,
            **kwargs
        )

    @contextlib.contextmanager
    def in_scope(self, scope: str) -> Iterator[None]:
        """
        Context manager to handle current scope.
        """
        old_scope = self.resolution_scope
        self.resolution_scope = urlparse.urljoin(old_scope, scope)
        try:
            yield
        finally:
            self.resolution_scope = old_scope

    @contextlib.contextmanager
    def resolving(self, ref: str) -> Iterator[Any]:
        """
        Context manager which resolves a JSON ``ref`` and enters the
        resolution scope of this ref.
        """
        new_uri = urlparse.urljoin(self.resolution_scope, ref)
        uri, fragment = urlparse.urldefrag(new_uri)

        document_uri = uri or self.base_uri

        if uri and normalize(uri) in self.store:
            schema = self.store[normalize(uri)]
        elif not uri or uri == self.base_uri:
            schema = self.schema
        else:
            schema = self._resolve_remote(uri)
            if self.cache:
                self.store[normalize(uri)] = schema

        old_base_uri, old_schema = self.base_uri, self.schema
        self.base_uri, self.schema = document_uri, schema
        try:
            with self.in_scope(document_uri):
                self._ensure_walked(document_uri, schema)
                if fragment and not fragment.startswith('/'):
                    plain_name = normalize(urlparse.urljoin(document_uri, '#' + fragment))
                    if plain_name in self.store:
                        yield self.store[plain_name]
                        return
                    raise JsonSchemaDefinitionException('Unresolvable ref: {}'.format(fragment))
                yield resolve_path(schema, fragment)
        finally:
            self.base_uri, self.schema = old_base_uri, old_schema

    def _resolve_remote(self, uri: str) -> Any:
        """
        Retrieve a remote document, applying the call-level policy when one
        is configured, and record provenance of the retrieval.
        """
        if self.ref_policy is None:
            schema = resolve_remote(uri, self.handlers)
            self._record(uri, 'ok', resource_id=resource_id_of(schema))
            return schema

        policy = self.ref_policy
        try:
            policy.check_target(uri)
            handler = self.handlers.get(urlparse.urlsplit(uri).scheme)
            if handler is not None:
                resource = handler_fetch(uri, handler, policy)
            else:
                resource = default_http_fetch(uri, policy)
            policy.check_resource_id(resource.final_uri or uri, resource.resource_id)
            self._remember(uri, resource.content)
        except RefRetrievalException as exc:
            self._record(uri, _STATUS_BY_EXCEPTION[type(exc)], reason=exc.reason)
            raise
        self._record(
            uri,
            'ok',
            final_uri=resource.final_uri,
            resource_id=resource.resource_id,
            size_bytes=resource.content_bytes,
        )
        return resource.content

    def _remember(self, uri: str, document: Any) -> None:
        """
        Remember the content retrieved for ``uri`` and raise
        :any:`RefContentConflict` when the same URI yields different content.
        """
        try:
            fingerprint = content_fingerprint(document)
        except (TypeError, ValueError) as exc:
            raise RefFetchError(
                'Ref to {!r} failed: document is not JSON serializable'.format(uri),
                uri=uri,
                reason='invalid_document',
            ) from exc
        normalized = normalize(uri)
        known = self._retrieved.get(normalized)
        if known is not None and known != fingerprint:
            raise RefContentConflict(
                'Ref to {!r} failed: URI previously resolved to different content'.format(uri),
                uri=uri,
                reason='content_conflict',
            )
        self._retrieved[normalized] = fingerprint

    def _record(self, uri: str, status: str, **details: Any) -> None:
        self.retrieval_log.append(RefRetrievalRecord(uri=uri, status=status, **details))

    def _ensure_walked(self, uri: str, schema: Any) -> None:
        normalized = normalize(uri) if uri else ''
        if normalized in self._walked_uris:
            return
        self.walk(schema, rewrite_refs=False)
        self._walked_uris.add(normalized)

    def _check_resource_id_conflict(self, normalized: str, node: dict[str, Any]) -> None:
        """
        Raise :any:`RefContentConflict` when the identity ``normalized`` is
        already bound to a document with different content.
        """
        existing = self.store.get(normalized)
        if existing is None or existing is node:
            return
        try:
            same = content_fingerprint(existing) == content_fingerprint(node)
        except (TypeError, ValueError):
            same = existing == node
        if not same:
            self._record(normalized, 'conflict', reason='resource_id_conflict')
            raise RefContentConflict(
                'Resource identity {!r} is claimed by documents with different content'.format(normalized),
                uri=normalized,
                reason='resource_id_conflict',
            )

    def get_uri(self) -> str:
        return normalize(self.resolution_scope)

    def get_scope_name(self) -> str:
        """
        Get current scope and return it as a valid function name.
        """
        name = 'validate_' + unquote(self.resolution_scope).replace('~1', '_').replace('~0', '_').replace('"', '')
        name = re.sub(r'($[^a-zA-Z]|[^a-zA-Z0-9])', '_', name)
        name = name.lower().rstrip('_')
        return name

    def walk(self, node: dict[str, Any] | bool, depth: int = 0, rewrite_refs: bool = True) -> None:
        """
        Walk thru schema and dereferencing ``id`` and ``$ref`` instances
        """
        if depth >= MAX_SCHEMA_WALK_DEPTH:
            raise JsonSchemaDefinitionException(
                'Schema is too deeply nested (maximum depth is {})'.format(MAX_SCHEMA_WALK_DEPTH)
            )

        if isinstance(node, bool):
            pass
        elif '$ref' in node and isinstance(node['$ref'], str):
            if rewrite_refs:
                ref = node['$ref']
                node['$ref'] = urlparse.urljoin(self.resolution_scope, ref)
        elif ('$id' in node or 'id' in node) and isinstance(get_id(node), str):
            with self.in_scope(get_id(node)):
                normalized = normalize(self.resolution_scope)
                if self.ref_policy is not None and self._retrieved:
                    self._check_resource_id_conflict(normalized, node)
                self.store[normalized] = node
                for _, item in node.items():
                    if isinstance(item, dict):
                        self.walk(item, depth + 1, rewrite_refs)
        else:
            for _, item in node.items():
                if isinstance(item, dict):
                    self.walk(item, depth + 1, rewrite_refs)
