import json
import urllib.request
from urllib.error import HTTPError, URLError

import pytest

from fastjsonschema import (
    JsonSchemaDefinitionException,
    JsonSchemaValueException,
    RefContentConflict,
    RefFetchError,
    RefPolicy,
    RefPolicyDenied,
    RefResolver,
    allow_uri_prefix,
    compile,
    compile_to_code,
    validate,
)
from fastjsonschema import ref_policy as ref_policy_module


def test_local_schema_is_not_affected_by_policy():
    definition = {
        'definitions': {'num': {'type': 'number'}},
        'type': 'object',
        'properties': {'a': {'$ref': '#/definitions/num'}},
    }
    validator = compile(definition, ref_policy=RefPolicy())
    assert validator({'a': 1}) == {'a': 1}
    assert validator.__ref_retrievals__ == []


def test_default_policy_denies_remote_ref():
    with pytest.raises(RefPolicyDenied) as exc:
        compile({'$ref': 'https://schemas.example.com/item.json'}, ref_policy=RefPolicy())
    assert exc.value.reason == 'uri_not_allowed'
    assert exc.value.uri == 'https://schemas.example.com/item.json'
    assert isinstance(exc.value, JsonSchemaDefinitionException)


def test_policy_exceptions_carry_uri_and_reason():
    log = []
    resolver = RefResolver.from_schema(
        {'$ref': 'https://schemas.example.com/item.json'},
        ref_policy=RefPolicy(),
        retrieval_log=log,
    )
    with pytest.raises(RefPolicyDenied):
        with resolver.resolving('https://schemas.example.com/item.json'):
            pass
    assert [(record.uri, record.status, record.reason) for record in log] == [
        ('https://schemas.example.com/item.json', 'denied', 'uri_not_allowed'),
    ]


def test_file_scheme_is_denied_even_when_uri_allowed():
    policy = RefPolicy(allowed_uri=lambda uri: True)
    with pytest.raises(RefPolicyDenied) as exc:
        compile({'$ref': 'file:///etc/passwd'}, ref_policy=policy)
    assert exc.value.reason == 'scheme_not_allowed'


def test_allowed_remote_ref_via_handler():
    repo = {'sch://item': {'$id': 'sch://item', 'type': 'string'}}
    policy = RefPolicy(allowed_uri=allow_uri_prefix('sch://'))
    validator = compile(
        {'$ref': 'sch://item'},
        handlers={'sch': repo.__getitem__},
        ref_policy=policy,
    )
    assert validator('ok') == 'ok'
    with pytest.raises(JsonSchemaValueException):
        validator(42)
    records = validator.__ref_retrievals__
    assert [(record.uri, record.status) for record in records] == [('sch://item', 'ok')]
    assert records[0].resource_id == 'sch://item'


def test_validate_and_compile_to_code_accept_ref_policy():
    with pytest.raises(RefPolicyDenied):
        validate({'$ref': 'https://schemas.example.com/item.json'}, None, ref_policy=RefPolicy())
    with pytest.raises(RefPolicyDenied):
        compile_to_code({'$ref': 'https://schemas.example.com/item.json'}, ref_policy=RefPolicy())


def test_response_size_limit_applies_to_handler_results():
    repo = {'sch://big': {'type': 'string', 'description': 'x' * 100}}
    policy = RefPolicy(allowed_uri=allow_uri_prefix('sch://'), max_response_bytes=10)
    with pytest.raises(RefPolicyDenied) as exc:
        compile({'$ref': 'sch://big'}, handlers={'sch': repo.__getitem__}, ref_policy=policy)
    assert exc.value.reason == 'response_too_large'


def test_resource_id_must_match_retrieval_uri_by_default():
    repo = {'sch://item': {'$id': 'sch://other', 'type': 'string'}}
    policy = RefPolicy(allowed_uri=allow_uri_prefix('sch://'))
    with pytest.raises(RefPolicyDenied) as exc:
        compile({'$ref': 'sch://item'}, handlers={'sch': repo.__getitem__}, ref_policy=policy)
    assert exc.value.reason == 'resource_id_mismatch'
    assert exc.value.resource_id == 'sch://other'


def test_resource_id_custom_predicate():
    repo = {'sch://item': {'$id': 'sch://other', 'type': 'string'}}
    policy = RefPolicy(
        allowed_uri=allow_uri_prefix('sch://'),
        allowed_resource_id=lambda uri, resource_id: resource_id.startswith('sch://'),
    )
    validator = compile(
        {'$ref': 'sch://item'},
        handlers={'sch': repo.__getitem__},
        ref_policy=policy,
    )
    assert validator('ok') == 'ok'


def test_resource_id_required():
    repo = {'sch://item': {'type': 'string'}}
    policy = RefPolicy(allowed_uri=allow_uri_prefix('sch://'), resource_id_required=True)
    with pytest.raises(RefPolicyDenied) as exc:
        compile({'$ref': 'sch://item'}, handlers={'sch': repo.__getitem__}, ref_policy=policy)
    assert exc.value.reason == 'resource_id_not_allowed'


def test_handler_failure_is_wrapped_with_reason():
    def handler(uri):
        raise KeyError(uri)

    policy = RefPolicy(allowed_uri=allow_uri_prefix('sch://'))
    with pytest.raises(RefFetchError) as exc:
        compile({'$ref': 'sch://missing'}, handlers={'sch': handler}, ref_policy=policy)
    assert exc.value.reason == 'handler_error'
    assert isinstance(exc.value.__cause__, KeyError)


def test_same_uri_with_different_content_conflicts():
    repo = {'sch://item': {'type': 'string'}}
    policy = RefPolicy(allowed_uri=allow_uri_prefix('sch://'))
    resolver = RefResolver.from_schema(
        {'$ref': 'sch://item'},
        handlers={'sch': repo.__getitem__},
        ref_policy=policy,
        cache=False,
    )
    with resolver.resolving('sch://item'):
        pass
    repo['sch://item'] = {'type': 'number'}
    with pytest.raises(RefContentConflict) as exc:
        with resolver.resolving('sch://item'):
            pass
    assert exc.value.reason == 'content_conflict'
    assert resolver.retrieval_log[-1].status == 'conflict'
    assert resolver.retrieval_log[-1].reason == 'content_conflict'


def test_remote_document_claiming_local_identity_conflicts():
    definition = {
        '$id': 'sch://main',
        'type': 'object',
        'properties': {'x': {'$ref': 'sch://remote'}},
    }
    repo = {
        'sch://remote': {
            '$id': 'sch://remote',
            'definitions': {'shadow': {'$id': 'sch://main', 'type': 'string'}},
        },
    }
    policy = RefPolicy(allowed_uri=allow_uri_prefix('sch://'))
    with pytest.raises(RefContentConflict) as exc:
        compile(definition, handlers={'sch': repo.__getitem__}, ref_policy=policy)
    assert exc.value.reason == 'resource_id_conflict'
    assert exc.value.uri == 'sch://main'


def test_legacy_behavior_without_policy_is_unchanged():
    repo = {'sch://item': {'type': 'string'}}
    validator = compile({'$ref': 'sch://item'}, handlers={'sch': repo.__getitem__})
    assert validator('ok') == 'ok'
    assert [record.status for record in validator.__ref_retrievals__] == ['ok']


def test_legacy_mode_does_not_check_resource_id_conflicts():
    definition = {
        '$id': 'sch://main',
        'type': 'object',
        'properties': {'x': {'$ref': 'sch://remote'}},
    }
    repo = {'sch://remote': {'$id': 'sch://main', 'type': 'string'}}
    validator = compile(definition, handlers={'sch': repo.__getitem__})
    assert validator({'x': 'ok'}) == {'x': 'ok'}


@pytest.mark.parametrize('allowed_hosts,uri,allowed', [
    (frozenset({'schemas.example.com'}), 'https://schemas.example.com/x', True),
    (frozenset({'schemas.example.com'}), 'https://other.example.com/x', False),
    (frozenset({'.example.com'}), 'https://schemas.example.com/x', True),
    (frozenset({'.example.com'}), 'https://example.com/x', True),
    (frozenset({'.example.com'}), 'https://notexample.com/x', False),
    (None, 'https://anything.example/x', True),
])
def test_allowed_hosts_matching(allowed_hosts, uri, allowed):
    policy = RefPolicy(allowed_hosts=allowed_hosts)
    if allowed:
        policy.check_host(uri)
    else:
        with pytest.raises(RefPolicyDenied) as exc:
            policy.check_host(uri)
        assert exc.value.reason == 'host_not_allowed'


def test_redirect_guard_denies_redirects():
    guard = ref_policy_module._RedirectGuard(
        RefPolicy(allowed_uri=lambda uri: True, allow_redirects=False)
    )
    with pytest.raises(RefPolicyDenied) as exc:
        guard.check('https://a.example/x', 'https://b.example/y')
    assert exc.value.reason == 'redirect_not_allowed'


def test_redirect_guard_limits_redirect_count():
    guard = ref_policy_module._RedirectGuard(
        RefPolicy(allowed_uri=lambda uri: True, max_redirects=1)
    )
    guard.check('https://a.example/1', 'https://a.example/2')
    with pytest.raises(RefPolicyDenied) as exc:
        guard.check('https://a.example/2', 'https://a.example/3')
    assert exc.value.reason == 'too_many_redirects'


def test_redirect_guard_rechecks_redirect_target():
    guard = ref_policy_module._RedirectGuard(
        RefPolicy(allowed_uri=allow_uri_prefix('https://allowed.example/'))
    )
    guard.check('https://allowed.example/x', 'https://allowed.example/y')
    with pytest.raises(RefPolicyDenied) as exc:
        guard.check('https://allowed.example/x', 'https://evil.example/y')
    assert exc.value.reason == 'uri_not_allowed'


class _FakeResponse:

    def __init__(self, body, url, code=200, msg='OK', headers=None):
        self._body = body
        self._url = url
        self.code = code
        self.msg = msg
        self._headers = headers or {}

    def read(self, limit=-1):
        if limit is None or limit < 0:
            return self._body
        return self._body[:limit]

    def info(self):
        return self._headers

    def geturl(self):
        return self._url

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _patch_opener(monkeypatch, response=None, error=None):
    class FakeOpener:
        def open(self, url, timeout=None):
            if error is not None:
                raise error
            return response

    monkeypatch.setattr('urllib.request.build_opener', lambda *handlers: FakeOpener())


def test_default_http_fetch_returns_resource_with_provenance(monkeypatch):
    body = json.dumps({'$id': 'https://schemas.example.com/item', 'type': 'string'}).encode()
    _patch_opener(monkeypatch, response=_FakeResponse(body, 'https://schemas.example.com/item'))
    policy = RefPolicy(allowed_uri=lambda uri: True)
    resource = ref_policy_module.default_http_fetch('https://schemas.example.com/item', policy)
    assert resource.content == {'$id': 'https://schemas.example.com/item', 'type': 'string'}
    assert resource.final_uri == 'https://schemas.example.com/item'
    assert resource.content_bytes == len(body)
    assert resource.resource_id == 'https://schemas.example.com/item'


def test_default_http_fetch_enforces_size_limit(monkeypatch):
    _patch_opener(monkeypatch, response=_FakeResponse(b'{"a": "' + b'x' * 100 + b'"}', 'https://x.example/'))
    policy = RefPolicy(allowed_uri=lambda uri: True, max_response_bytes=10)
    with pytest.raises(RefPolicyDenied) as exc:
        ref_policy_module.default_http_fetch('https://x.example/', policy)
    assert exc.value.reason == 'response_too_large'


def test_default_http_fetch_wraps_invalid_json(monkeypatch):
    _patch_opener(monkeypatch, response=_FakeResponse(b'not json', 'https://x.example/'))
    policy = RefPolicy(allowed_uri=lambda uri: True)
    with pytest.raises(RefFetchError) as exc:
        ref_policy_module.default_http_fetch('https://x.example/', policy)
    assert exc.value.reason == 'invalid_json'


def test_default_http_fetch_wraps_network_errors(monkeypatch):
    _patch_opener(monkeypatch, error=URLError('connection refused'))
    policy = RefPolicy(allowed_uri=lambda uri: True)
    with pytest.raises(RefFetchError) as exc:
        ref_policy_module.default_http_fetch('https://x.example/', policy)
    assert exc.value.reason == 'network_error'
    assert isinstance(exc.value.__cause__, URLError)


def test_default_http_fetch_wraps_http_errors(monkeypatch):
    error = HTTPError('https://x.example/', 404, 'Not Found', {}, None)
    _patch_opener(monkeypatch, error=error)
    policy = RefPolicy(allowed_uri=lambda uri: True)
    with pytest.raises(RefFetchError) as exc:
        ref_policy_module.default_http_fetch('https://x.example/', policy)
    assert exc.value.reason == 'http_error'
    assert exc.value.status == 404


def _patch_transport(monkeypatch, routes):
    """
    Replace the transport of the opener built by ``default_http_fetch`` with
    a fake serving ``routes`` (mapping of URL to ``_FakeResponse``), while
    keeping the real urllib machinery -- including the policy-guarded
    redirect handler -- in place.
    """
    real_build_opener = urllib.request.build_opener

    class FakeHTTPHandler(urllib.request.HTTPHandler):
        def http_open(self, req):
            return routes[req.full_url]

    def fake_build_opener(*handlers):
        return real_build_opener(*handlers, FakeHTTPHandler())

    monkeypatch.setattr('urllib.request.build_opener', fake_build_opener)


def test_redirect_is_followed_and_recorded(monkeypatch):
    body = json.dumps({'type': 'string'}).encode()
    _patch_transport(monkeypatch, {
        'http://schemas.example/redirect': _FakeResponse(
            b'', 'http://schemas.example/redirect',
            code=302, msg='Found', headers={'location': '/final'},
        ),
        'http://schemas.example/final': _FakeResponse(body, 'http://schemas.example/final'),
    })
    policy = RefPolicy(allowed_uri=allow_uri_prefix('http://schemas.example/'))
    validator = compile({'$ref': 'http://schemas.example/redirect'}, ref_policy=policy)
    assert validator('ok') == 'ok'
    records = validator.__ref_retrievals__
    assert [(record.uri, record.status) for record in records] == [
        ('http://schemas.example/redirect', 'ok'),
    ]
    assert records[0].final_uri == 'http://schemas.example/final'
    assert records[0].size_bytes == len(body)


def test_redirect_is_denied_when_policy_disallows(monkeypatch):
    _patch_transport(monkeypatch, {
        'http://schemas.example/redirect': _FakeResponse(
            b'', 'http://schemas.example/redirect',
            code=302, msg='Found', headers={'location': '/final'},
        ),
    })
    policy = RefPolicy(
        allowed_uri=allow_uri_prefix('http://schemas.example/'),
        allow_redirects=False,
    )
    with pytest.raises(RefPolicyDenied) as exc:
        compile({'$ref': 'http://schemas.example/redirect'}, ref_policy=policy)
    assert exc.value.reason == 'redirect_not_allowed'


def test_redirect_to_disallowed_target_is_denied(monkeypatch):
    _patch_transport(monkeypatch, {
        'http://schemas.example/redirect': _FakeResponse(
            b'', 'http://schemas.example/redirect',
            code=302, msg='Found', headers={'location': 'http://169.254.169.254/latest/meta-data'},
        ),
    })
    policy = RefPolicy(allowed_uri=allow_uri_prefix('http://schemas.example/'))
    with pytest.raises(RefPolicyDenied) as exc:
        compile({'$ref': 'http://schemas.example/redirect'}, ref_policy=policy)
    assert exc.value.reason == 'uri_not_allowed'


def test_response_size_limit_is_enforced_over_http(monkeypatch):
    _patch_transport(monkeypatch, {
        'http://schemas.example/big': _FakeResponse(
            b'{"description": "' + b'x' * 1000 + b'"}', 'http://schemas.example/big',
        ),
    })
    policy = RefPolicy(
        allowed_uri=allow_uri_prefix('http://schemas.example/'),
        max_response_bytes=100,
    )
    with pytest.raises(RefPolicyDenied) as exc:
        compile({'$ref': 'http://schemas.example/big'}, ref_policy=policy)
    assert exc.value.reason == 'response_too_large'
