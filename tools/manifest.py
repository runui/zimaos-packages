"""Read package manifests and describe their inputs without running recipes."""

import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
VALUE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.+-]*')
NAME = re.compile(r'[a-z0-9][a-z0-9_-]*')


def read_json(path):
    return json.loads(Path(path).read_text())


def checked_path(directory, value):
    path = Path(value)
    if path.is_absolute() or '..' in path.parts or '\n' in value or '\t' in value:
        raise ValueError(f'invalid package input path: {value!r}')
    result = directory / path
    if not (result.is_file() or result.is_symlink()):
        raise ValueError(f'package input is missing: {result}')
    return result


def command(value):
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value) or not value[0]:
        raise ValueError('commands must be nonempty arrays of strings')
    return value


def load_package(root, name, architecture=None):
    if not NAME.fullmatch(name):
        raise ValueError(f'invalid package name: {name!r}')
    directory = root / 'packages' / name
    package = read_json(directory / 'package.json')
    if package['name'] != name or not VALUE.fullmatch(package['version']):
        raise ValueError('invalid package identity')
    if type(package['release']) is not int or package['release'] < 1:
        raise ValueError('package release must be a positive integer')
    if not re.fullmatch(r'v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?', package['module_version']):
        raise ValueError('module_version must use SemVer')
    if 'sources' in package:
        if 'source' in package or 'architecture' in package:
            raise ValueError('sources cannot be combined with source or architecture')
        sources = package['sources']
    else:
        sources = {package['architecture']: package['source']}
    if not isinstance(sources, dict) or not sources or not all(VALUE.fullmatch(key) for key in sources):
        raise ValueError('sources must map architecture names to pinned inputs')
    for source in sources.values():
        if not VALUE.fullmatch(source['filename']) or not re.fullmatch(r'[0-9a-f]{128}', source['sha512']):
            raise ValueError('invalid source filename or SHA-512')
        if not source['url'].startswith('https://'):
            raise ValueError('upstream sources must use HTTPS')
    command(package['build']['command'])
    for value in package['build']['inputs']:
        checked_path(directory, value)
    for value in package['build'].get('artifacts', []):
        if not VALUE.fullmatch(value) or value in {
                'raw', 'stage.tar', 'details.json', 'image.tar', 'module.json', 'build-info.json',
                'NOTICE', 'SHA256SUMS', 'fetch.json', 'stage.json', 'assemble.json', 'image.json', 'test.json'} or value.startswith('source-'):
            raise ValueError(f'invalid artifact basename: {value!r}')
    if set(package.get('tests', {})) - {'smoke', 'integration'}:
        raise ValueError('test suites must be named smoke or integration')
    for value in package.get('tests', {}).values():
        command(value)
    if not package['targets'] or not all(NAME.fullmatch(value) for value in package['targets']):
        raise ValueError('package targets must be valid target names')
    if architecture is not None:
        if architecture not in sources:
            raise ValueError(f'package does not support architecture: {architecture}')
        package = {key: value for key, value in package.items() if key != 'sources'}
        package.update(architecture=architecture, source=sources[architecture])
    return package


def load_target(root, name):
    if not NAME.fullmatch(name):
        raise ValueError(f'invalid target name: {name!r}')
    target = read_json(root / 'targets' / (name + '.json'))
    for field in ('os_id', 'architecture', 'sysext_architecture'):
        if not VALUE.fullmatch(target[field]):
            raise ValueError(f'invalid target field: {field}')
    return target


def extension_release(target):
    # systemd requires VERSION_ID to match when ID names a specific host OS.
    # _any is its supported form for extensions with no host version dependency;
    # the supported OS remains explicit in the target and build provenance.
    return ('ID=_any\n' +
            f"ARCHITECTURE={target['sysext_architecture']}\nSYSEXT_SCOPE=system\n")


def payload_files(directory):
    payload = directory / 'files'
    return sorted(path for path in payload.rglob('*') if path.is_file() or path.is_symlink())
