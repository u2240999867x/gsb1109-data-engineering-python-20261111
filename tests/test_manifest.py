import json
import subprocess
import sys

import pytest

from fastjsonschema import (
    JsonSchemaException,
    JsonSchemaValueException,
    VERSION,
    compile_to_code,
    compile_to_code_with_manifest,
    compile_with_manifest,
    verify_manifest,
)


REMOTE_SCHEMA = {
    'type': 'object',
    'properties': {'x': {'type': 'number', 'default': 1}},
    'required': ['x'],
}


def make_remote_definition():
    return {
        'type': 'object',
        'properties': {'point': {'$ref': 'test://schemas/point'}},
    }


def make_handlers(remote=REMOTE_SCHEMA):
    return {'test': lambda uri: remote}


def test_compile_with_manifest_records_options_and_root_schema():
    validator, manifest = compile_with_manifest(
        {'type': 'object', 'properties': {'a': {'type': 'number', 'default': 42}}},
        use_default=False,
        use_formats=False,
        detailed_exceptions=False,
        fast_fail=False,
    )
    assert validator({}) == {}
    assert manifest['manifest_version'] == 1
    assert manifest['fastjsonschema_version'] == VERSION
    assert manifest['generator'].startswith('CodeGeneratorDraft')
    assert manifest['options'] == {
        'use_default': False,
        'use_formats': False,
        'detailed_exceptions': False,
        'fast_fail': False,
    }
    assert manifest['schema']['schema']['type'] == 'object'
    assert isinstance(manifest['schema']['sha256'], str)
    assert manifest['external_refs'] == {}
    json.dumps(manifest)


def test_manifest_embeds_external_refs_not_only_root_digest():
    _, manifest_one = compile_with_manifest(make_remote_definition(), handlers=make_handlers())
    _, manifest_two = compile_with_manifest(
        make_remote_definition(),
        handlers=make_handlers({'type': 'string'}),
    )
    # Root schemas are identical, so a root-only digest could not tell them apart.
    assert manifest_one['schema']['sha256'] == manifest_two['schema']['sha256']
    ref_one = manifest_one['external_refs']['test://schemas/point']
    ref_two = manifest_two['external_refs']['test://schemas/point']
    assert ref_one['schema'] == REMOTE_SCHEMA
    assert ref_one['sha256'] != ref_two['sha256']
    assert manifest_one != manifest_two


def test_manifest_records_nested_remote_refs():
    handlers = {
        'one': lambda uri: {'properties': {'nested': {'$ref': 'two://schemas/other'}}},
        'two': lambda uri: {'type': 'integer'},
    }
    _, manifest = compile_with_manifest({'$ref': 'one://schemas/first'}, handlers=handlers)
    assert sorted(manifest['external_refs']) == ['one://schemas/first', 'two://schemas/other']
    assert manifest['external_refs']['two://schemas/other']['schema'] == {'type': 'integer'}


def test_manifest_records_formats():
    def my_check(value):
        return True

    validator, manifest = compile_with_manifest(
        {
            'type': 'object',
            'properties': {
                'mail': {'type': 'string', 'format': 'email'},
                'tag': {'type': 'string', 'format': 'taggy'},
                'mine': {'type': 'string', 'format': 'mine'},
            },
        },
        formats={'taggy': r'a|b', 'mine': my_check},
    )
    assert validator({'mail': 'a@b.c', 'taggy': 'a', 'mine': 'x'})
    assert manifest['formats']['used'] == ['email', 'mine', 'taggy']
    assert manifest['formats']['custom']['taggy'] == {'kind': 'regex', 'pattern': 'a|b'}
    assert manifest['formats']['custom']['mine'] == {'kind': 'callable', 'name': 'my_check'}


def test_compile_to_code_with_manifest_matches_compile_to_code():
    code, manifest = compile_to_code_with_manifest(
        make_remote_definition(), handlers=make_handlers()
    )
    assert code == compile_to_code(make_remote_definition(), handlers=make_handlers())
    assert verify_manifest(manifest, code) is True


def test_generated_code_runs_standalone_without_manifest():
    code, _ = compile_to_code_with_manifest(
        make_remote_definition(), handlers=make_handlers()
    )
    namespace = {}
    exec(code, namespace)
    validate = namespace['validate']
    assert validate({'point': {'x': 2}}) == {'point': {'x': 2}}
    with pytest.raises(JsonSchemaValueException):
        validate({'point': {}})


def test_verify_manifest_rejects_tampered_code():
    code, manifest = compile_to_code_with_manifest({'type': 'string'})
    with pytest.raises(JsonSchemaException, match='code_sha256'):
        verify_manifest(manifest, code + '\n# tampered\n')


def test_verify_manifest_rejects_tampered_embedded_schemas():
    _, manifest = compile_with_manifest(make_remote_definition(), handlers=make_handlers())
    manifest['schema']['schema']['type'] = 'string'
    with pytest.raises(JsonSchemaException, match='digest'):
        verify_manifest(manifest)

    _, manifest = compile_with_manifest(make_remote_definition(), handlers=make_handlers())
    manifest['external_refs']['test://schemas/point']['schema']['required'] = []
    with pytest.raises(JsonSchemaException, match='digest'):
        verify_manifest(manifest)


def test_verify_manifest_rejects_unknown_version_and_garbage():
    with pytest.raises(JsonSchemaException, match='manifest must be an object'):
        verify_manifest('not-a-manifest')
    with pytest.raises(JsonSchemaException, match='manifest version'):
        verify_manifest({'manifest_version': 999})


def test_verify_manifest_never_touches_network(monkeypatch):
    code, manifest = compile_to_code_with_manifest(
        make_remote_definition(), handlers=make_handlers()
    )

    def forbidden(*args, **kwargs):
        raise AssertionError('network access during verification')

    monkeypatch.setattr('urllib.request.urlopen', forbidden)
    assert verify_manifest(manifest, code) is True
    assert verify_manifest(json.loads(json.dumps(manifest)), code) is True


def test_cli_writes_manifest(tmp_path):
    manifest_file = tmp_path / 'manifest.json'
    result = subprocess.run(
        [sys.executable, '-m', 'fastjsonschema', '--manifest', str(manifest_file),
         '{"type": "string", "format": "email"}'],
        capture_output=True,
        text=True,
        check=True,
    )
    manifest = json.loads(manifest_file.read_text())
    assert manifest['formats']['used'] == ['email']
    # print() appends exactly one newline to the generated code.
    assert result.stdout.endswith('\n')
    assert verify_manifest(manifest, result.stdout[:-1]) is True
