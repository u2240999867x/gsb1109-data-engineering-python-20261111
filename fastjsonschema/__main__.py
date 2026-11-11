import json
import sys

from . import compile_to_code, compile_to_code_with_manifest


def main():
    args = sys.argv[1:]
    manifest_file = None
    if args[:1] == ['--manifest']:
        manifest_file = args[1]
        args = args[2:]

    if args:
        definition = args[0]
    else:
        definition = sys.stdin.read()

    definition = json.loads(definition)
    if manifest_file is None:
        print(compile_to_code(definition))
    else:
        code, manifest = compile_to_code_with_manifest(definition)
        with open(manifest_file, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, indent=2, sort_keys=True)
        print(code)


if __name__ == '__main__':
    main()
