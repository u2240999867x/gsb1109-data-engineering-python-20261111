"""
Companion manifests for compiled validators.

When a schema is compiled to Python code and shipped to an offline worker,
it is hard to tell afterwards which external references, format checks and
options were in effect. A manifest is a plain-data (JSON serializable)
document generated next to the code which records exactly that:

 * the effective root schema and its digest,
 * every remote reference really fetched while compiling, embedded with
   its content and digest (not only the root schema is hashed),
 * the formats in effect (built-in format keywords used by the schemas and
   the custom formats passed to the compiler),
 * the compile options (``use_default``, ``use_formats``,
   ``detailed_exceptions``, ``fast_fail``),
 * and the digest of the generated code.

The manifest is declarative: it contains only data, never instructions.
Nothing in it tells a worker to download or execute anything, and
:any:`verify_manifest` performs no network access and no dynamic code
loading -- remote schemas are embedded in the manifest itself, so a
deployment can be checked fully offline before the validator is run.
"""
import hashlib
import json
from typing import Any

from .exceptions import JsonSchemaException
from .version import VERSION

MANIFEST_VERSION = 1


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def hash_value(value: Any) -> str:
    """
    Digest of a JSON-compatible value with canonical key ordering.
    """
    return hashlib.sha256(_canonical_json(value).encode('utf-8')).hexdigest()


def hash_text(text: str) -> str:
    """
    Digest of generated source code.
    """
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _describe_custom_formats(formats: dict) -> dict:
    described = {}
    for name, fmt in sorted(formats.items()):
        if isinstance(fmt, str):
            described[name] = {'kind': 'regex', 'pattern': fmt}
        else:
            # A callable cannot be serialized; record its presence and name
            # so the deployment contract at least declares it is required.
            described[name] = {
                'kind': 'callable',
                'name': getattr(fmt, '__name__', type(fmt).__name__),
            }
    return described


def _collect_format_keywords(node: Any, found: set) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == 'format' and isinstance(value, str):
                found.add(value)
            else:
                _collect_format_keywords(value, found)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _collect_format_keywords(item, found)


def create_manifest(
    definition: Any,
    resolver: Any,
    code: str,
    formats: dict,
    options: dict,
    generator_name: str,
) -> dict:
    """
    Build a manifest for already generated ``code``. The ``resolver`` must be
    the one used during compilation, so every remote reference that was really
    fetched is embedded next to the (possibly rewritten) root ``definition``.
    """
    external_refs = {
        uri: {'sha256': hash_value(schema), 'schema': schema}
        for uri, schema in sorted(resolver.resolved_remote.items())
    }
    used_formats: set = set()
    _collect_format_keywords(definition, used_formats)
    for entry in external_refs.values():
        _collect_format_keywords(entry['schema'], used_formats)
    return {
        'manifest_version': MANIFEST_VERSION,
        'fastjsonschema_version': VERSION,
        'generator': generator_name,
        'options': dict(options),
        'schema': {'sha256': hash_value(definition), 'schema': definition},
        'external_refs': external_refs,
        'formats': {
            'used': sorted(used_formats),
            'custom': _describe_custom_formats(formats),
        },
        'code_sha256': hash_text(code),
    }


def verify_manifest(manifest: dict, code: str | None = None) -> bool:
    """
    Check a manifest before running the deployed validator. Verifies that the
    embedded root schema and every embedded external reference still match
    their recorded digests, and -- when ``code`` is given -- that the deployed
    source matches the recorded code digest. Raises :any:`JsonSchemaException`
    on any mismatch. Pure data check: no network access, no code execution.
    """
    if not isinstance(manifest, dict):
        raise JsonSchemaException('manifest must be an object')
    if manifest.get('manifest_version') != MANIFEST_VERSION:
        raise JsonSchemaException(
            'unsupported manifest version: {!r}'.format(manifest.get('manifest_version'))
        )
    _verify_entry('schema', manifest.get('schema'))
    external_refs = manifest.get('external_refs')
    if not isinstance(external_refs, dict):
        raise JsonSchemaException('manifest external_refs must be an object')
    for uri, entry in external_refs.items():
        _verify_entry('external reference {}'.format(uri), entry)
    if code is not None:
        expected = manifest.get('code_sha256')
        if not isinstance(expected, str):
            raise JsonSchemaException('manifest is missing code_sha256')
        if hash_text(code) != expected:
            raise JsonSchemaException('deployed code does not match manifest code_sha256')
    return True


def _verify_entry(what: str, entry: Any) -> None:
    if not isinstance(entry, dict) or 'schema' not in entry or 'sha256' not in entry:
        raise JsonSchemaException('manifest entry for {} is malformed'.format(what))
    if hash_value(entry['schema']) != entry['sha256']:
        raise JsonSchemaException('{} does not match its recorded digest'.format(what))
