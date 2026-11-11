"""
Call-level policy controlling how remote ``$ref`` references are retrieved.

A ``RefPolicy`` is passed per ``compile``/``validate``/``compile_to_code``
call (via the ``ref_policy`` argument) and decides, for every remote
reference encountered while compiling that particular schema:

 * which target URIs may be fetched at all (``allowed_uri``,
   ``allowed_schemes``, ``allowed_hosts``),
 * whether and how redirects may be followed (``allow_redirects``,
   ``max_redirects``),
 * how large a response may be (``max_response_bytes``),
 * and which resource identity (``$id``/``id``) a fetched document may
   claim (``allowed_resource_id``, ``resource_id_required``).

The policy is applied by the resolver itself; no global network function is
patched, so other code (or other compilations without a policy) keeps its
previous behavior. Purely local schemas (no remote ``$ref``) never trigger
the policy.
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urldefrag, urlsplit

from .exceptions import RefFetchError, RefPolicyDenied, RefRetrievalException

DEFAULT_MAX_RESPONSE_BYTES = 5 * 1024 * 1024
DEFAULT_MAX_REDIRECTS = 5
DEFAULT_TIMEOUT = 10.0


@dataclass
class FetchedResource:
    """
    A remote document retrieved for a ``$ref``, together with its provenance.

    ``uri`` is the URI that was requested, ``final_uri`` the URI after any
    redirects, ``content`` the decoded document, ``content_bytes`` the size
    of the response body in bytes (``None`` when the document was produced
    by a custom handler without transport), ``resource_id`` the identity
    the document declares via ``$id``/``id`` (if any) and ``from_handler``
    whether a custom handler supplied the document.
    """

    uri: str
    final_uri: str
    content: Any
    content_bytes: int | None
    resource_id: str | None
    from_handler: bool = False


@dataclass
class RefRetrievalRecord:
    """
    Provenance record of a single remote reference retrieval attempt.

    ``status`` is one of ``ok``, ``denied``, ``failed`` or ``conflict``;
    ``reason`` mirrors the ``reason`` attribute of the raised exception for
    non-``ok`` records. Successful records carry the ``final_uri``, the
    declared ``resource_id`` and the response ``size_bytes``.
    """

    uri: str
    status: str
    reason: str | None = None
    final_uri: str | None = None
    resource_id: str | None = None
    size_bytes: int | None = None


def allow_uri_prefix(*prefixes: str) -> Callable[[str], bool]:
    """
    Build an ``allowed_uri`` predicate permitting exactly the given URI
    prefixes. Example: ``allow_uri_prefix('https://schemas.example.com/')``.
    """
    def predicate(uri: str) -> bool:
        return any(uri.startswith(prefix) for prefix in prefixes)
    return predicate


def resource_id_of(document: Any) -> str | None:
    """
    Return the identity a document declares via ``$id`` (or legacy ``id``),
    normalized and stripped of any fragment. ``None`` if it declares none.
    """
    if not isinstance(document, dict):
        return None
    declared = document.get('$id', document.get('id'))
    if not isinstance(declared, str) or not declared:
        return None
    return urldefrag(urlsplit(declared).geturl())[0]


def content_fingerprint(document: Any) -> str:
    """
    Stable fingerprint of a retrieved document, used to detect the same URI
    resolving to different content.
    """
    canonical = json.dumps(document, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _estimate_size(document: Any) -> int | None:
    try:
        return len(json.dumps(document).encode('utf-8'))
    except (TypeError, ValueError):
        return None


@dataclass
class RefPolicy:
    """
    Policy deciding whether and how a remote ``$ref`` may be retrieved.

    The default policy denies every remote reference: a contract coming
    from an external party can never make the compilation process reach an
    address it names unless the caller explicitly opts in, e.g.::

        policy = RefPolicy(allowed_uri=allow_uri_prefix('https://schemas.example.com/'))
        validator = fastjsonschema.compile(definition, ref_policy=policy)

    Attributes:

     * ``allowed_uri`` -- predicate receiving the (normalized) target URI;
       remote references are denied unless it returns ``True``.
     * ``allowed_schemes`` -- schemes that may be fetched with the built-in
       fetcher (custom ``handlers`` are not restricted by this).
     * ``allowed_hosts`` -- optional iterable of host names the built-in
       fetcher may contact; an entry starting with ``.`` also matches its
       subdomains. ``None`` means no host restriction.
     * ``allow_redirects`` / ``max_redirects`` -- redirect handling of the
       built-in fetcher; every redirect target is checked against
       ``allowed_uri`` again.
     * ``max_response_bytes`` -- maximum accepted response size; ``None``
       disables the limit.
     * ``allowed_resource_id`` -- optional predicate for the identity a
       fetched document declares via ``$id``/``id``. When ``None`` (the
       default), a declared identity must match the URI the document was
       retrieved from.
     * ``resource_id_required`` -- deny documents that declare no identity
       at all.
     * ``timeout`` -- timeout in seconds for the built-in fetcher.
    """

    allowed_uri: Callable[[str], bool] | None = None
    allowed_schemes: frozenset[str] = frozenset({'http', 'https'})
    allowed_hosts: frozenset[str] | None = None
    allow_redirects: bool = True
    max_redirects: int = DEFAULT_MAX_REDIRECTS
    max_response_bytes: int | None = DEFAULT_MAX_RESPONSE_BYTES
    allowed_resource_id: Callable[[str, str], bool] | None = None
    resource_id_required: bool = False
    timeout: float = DEFAULT_TIMEOUT

    def check_target(self, uri: str) -> None:
        """
        Raise :any:`RefPolicyDenied` unless the policy allows fetching ``uri``.
        """
        if self.allowed_uri is None or not self.allowed_uri(uri):
            raise RefPolicyDenied(
                'Ref to {!r} denied by policy: URI is not in the allowed set'.format(uri),
                uri=uri,
                reason='uri_not_allowed',
            )

    def check_scheme(self, uri: str) -> None:
        """
        Raise :any:`RefPolicyDenied` if the built-in fetcher may not handle
        the scheme of ``uri``.
        """
        scheme = urlsplit(uri).scheme
        if scheme not in self.allowed_schemes:
            raise RefPolicyDenied(
                'Ref to {!r} denied by policy: scheme {!r} is not allowed'.format(uri, scheme),
                uri=uri,
                reason='scheme_not_allowed',
                scheme=scheme,
            )

    def check_host(self, uri: str) -> None:
        """
        Raise :any:`RefPolicyDenied` if the host of ``uri`` is not in
        ``allowed_hosts``.
        """
        if self.allowed_hosts is None:
            return
        host = (urlsplit(uri).hostname or '').lower()
        for entry in self.allowed_hosts:
            entry = entry.lower()
            if entry.startswith('.'):
                if host == entry[1:] or host.endswith(entry):
                    return
            elif host == entry:
                return
        raise RefPolicyDenied(
            'Ref to {!r} denied by policy: host {!r} is not allowed'.format(uri, host),
            uri=uri,
            reason='host_not_allowed',
            host=host,
        )

    def check_size(self, uri: str, size: int | None) -> None:
        """
        Raise :any:`RefPolicyDenied` if ``size`` exceeds ``max_response_bytes``.
        """
        if self.max_response_bytes is None or size is None:
            return
        if size > self.max_response_bytes:
            raise RefPolicyDenied(
                'Ref to {!r} denied by policy: response of {} bytes exceeds the limit of {} bytes'.format(
                    uri, size, self.max_response_bytes,
                ),
                uri=uri,
                reason='response_too_large',
                size=size,
                max_response_bytes=self.max_response_bytes,
            )

    def check_resource_id(self, uri: str, resource_id: str | None) -> None:
        """
        Raise :any:`RefPolicyDenied` if the identity declared by the fetched
        document is not acceptable for the URI it was retrieved from.
        """
        if resource_id is None:
            if self.resource_id_required:
                raise RefPolicyDenied(
                    'Ref to {!r} denied by policy: document declares no resource identity ($id/id)'.format(uri),
                    uri=uri,
                    reason='resource_id_not_allowed',
                )
            return
        if self.allowed_resource_id is not None:
            if not self.allowed_resource_id(uri, resource_id):
                raise RefPolicyDenied(
                    'Ref to {!r} denied by policy: resource identity {!r} is not allowed'.format(uri, resource_id),
                    uri=uri,
                    reason='resource_id_not_allowed',
                    resource_id=resource_id,
                )
            return
        if urldefrag(urlsplit(uri).geturl())[0] != resource_id:
            raise RefPolicyDenied(
                'Ref to {!r} denied by policy: document claims identity {!r}'.format(uri, resource_id),
                uri=uri,
                reason='resource_id_mismatch',
                resource_id=resource_id,
            )


class _RedirectGuard:
    """
    Per-request redirect checker enforcing the policy on every redirect hop.

    Instantiated per fetch; nothing global (no opener, no ``urlopen``
    default) is modified.
    """

    def __init__(self, policy: RefPolicy) -> None:
        self._policy = policy
        self._redirects = 0

    def check(self, url: str, newurl: str) -> None:
        """
        Raise :any:`RefPolicyDenied` if a redirect from ``url`` to ``newurl``
        violates the policy.
        """
        if not self._policy.allow_redirects:
            raise RefPolicyDenied(
                'Ref to {!r} denied by policy: redirect to {!r} is not allowed'.format(url, newurl),
                uri=url,
                reason='redirect_not_allowed',
                redirect_target=newurl,
            )
        self._redirects += 1
        if self._redirects > self._policy.max_redirects:
            raise RefPolicyDenied(
                'Ref to {!r} denied by policy: more than {} redirects'.format(url, self._policy.max_redirects),
                uri=url,
                reason='too_many_redirects',
                max_redirects=self._policy.max_redirects,
            )
        self._policy.check_target(newurl)
        self._policy.check_scheme(newurl)
        self._policy.check_host(newurl)


def default_http_fetch(uri: str, policy: RefPolicy) -> FetchedResource:
    """
    Fetch ``uri`` with urllib under the given policy.

    Used for remote references when no custom handler is registered for the
    URI scheme. The policy controls the target, redirects and the response
    size; a fresh opener is built per call so no global state is touched.
    """
    from urllib.error import HTTPError, URLError
    from urllib.request import HTTPRedirectHandler, build_opener

    policy.check_scheme(uri)
    policy.check_host(uri)

    guard = _RedirectGuard(policy)

    class GuardedRedirectHandler(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            guard.check(req.full_url, newurl)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    opener = build_opener(GuardedRedirectHandler())
    try:
        with opener.open(uri, timeout=policy.timeout) as response:
            body = response.read(policy.max_response_bytes + 1 if policy.max_response_bytes is not None else -1)
            final_uri = response.geturl()
    except RefRetrievalException:
        raise
    except HTTPError as exc:
        raise RefFetchError(
            'Ref to {!r} failed: HTTP error {}'.format(uri, exc.code),
            uri=uri,
            reason='http_error',
            status=exc.code,
        ) from exc
    except (URLError, OSError, ValueError) as exc:
        raise RefFetchError(
            'Ref to {!r} failed: {}'.format(uri, exc),
            uri=uri,
            reason='network_error',
        ) from exc

    policy.check_size(uri, len(body))
    try:
        content = json.loads(body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError) as exc:
        raise RefFetchError(
            'Ref to {!r} failed: response is not valid JSON'.format(uri),
            uri=uri,
            reason='invalid_json',
        ) from exc
    return FetchedResource(
        uri=uri,
        final_uri=final_uri,
        content=content,
        content_bytes=len(body),
        resource_id=resource_id_of(content),
    )


def handler_fetch(uri: str, handler: Callable[[str], Any], policy: RefPolicy) -> FetchedResource:
    """
    Retrieve ``uri`` through a user-supplied handler, still enforcing the
    policy's size and resource-identity rules on the result.
    """
    try:
        content = handler(uri)
    except RefRetrievalException:
        raise
    except Exception as exc:
        raise RefFetchError(
            'Ref to {!r} failed: handler raised {!r}'.format(uri, exc),
            uri=uri,
            reason='handler_error',
        ) from exc
    size = _estimate_size(content)
    policy.check_size(uri, size)
    return FetchedResource(
        uri=uri,
        final_uri=uri,
        content=content,
        content_bytes=size,
        resource_id=resource_id_of(content),
        from_handler=True,
    )
