#!/usr/bin/env python3
"""Validate distribution bundles and assemble a static ZimaOS module store."""

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from urllib.parse import quote, urlsplit

from common import digest, write_json
from manifest import ROOT, VALUE, extension_release, load_package, load_target, read_json

MAX_FILE_BYTES = 100 * 1024 * 1024
RUNNERS = {'x86_64': 'ubuntu-24.04', 'aarch64': 'ubuntu-24.04-arm'}


def repository_path(target):
    architecture = {'x86_64': 'amd64', 'aarch64': 'arm64'}[target['architecture']]
    return Path(target['os_id']) / architecture


def catalog(root):
    config = read_json(root / 'repository.json')
    packages = [load_package(root, path.parent.name)
                for path in sorted((root / 'packages').glob('*/package.json'))]
    targets = config['targets']
    if not isinstance(targets, list) or not targets or len(set(targets)) != len(targets):
        raise ValueError('repository targets must be a nonempty list of unique target names')
    matrix = []
    paths = set()
    for target_name in targets:
        target = load_target(root, target_name)
        path = repository_path(target)
        if path in paths:
            raise ValueError('repository targets must use distinct OS/architecture directories')
        paths.add(path)
        selected = [package for package in packages if target_name in package['targets']]
        if not selected:
            raise ValueError(f'no packages support {target_name}')
        for package in selected:
            package = load_package(root, package['name'], target['architecture'])
            matrix.append({'package': package['name'], 'target': target_name,
                           'runner': RUNNERS[target['architecture']]})
    return config, matrix


def unpack_bundle(bundle, destination):
    with tarfile.open(bundle) as archive:
        members = archive.getmembers()
        names = set()
        for member in members:
            if (not member.isfile() or not VALUE.fullmatch(member.name)
                    or member.name in names or member.size >= MAX_FILE_BYTES):
                raise ValueError(f'invalid or oversized distribution member: {member.name}')
            names.add(member.name)
        for member in members:
            with archive.extractfile(member) as source, (destination / member.name).open('wb') as output:
                shutil.copyfileobj(source, output)


def image_file(image, name):
    return subprocess.run(['unsquashfs', '-cat', str(image), name],
                          check=True, capture_output=True).stdout


def verify_bundle(directory, root, expected):
    provenance = list(directory.glob('*.build-info.json'))
    if len(provenance) != 1:
        raise ValueError('bundle must contain exactly one build-info file')
    info = read_json(provenance[0])
    identity = (info['package'], info['target'])
    if identity not in expected:
        raise ValueError(f'unexpected or duplicate package compatibility: {identity}')
    target = load_target(root, info['target'])
    if info['os_id'] != target['os_id']:
        raise ValueError('bundle OS identity differs from target')
    package = load_package(root, info['package'], target['architecture'])
    if info['manifest'] != package:
        raise ValueError('resolved manifest differs from package definition')
    name = package['name']
    for field in ('version', 'release', 'module_version', 'architecture', 'source'):
        if info[field] != package[field]:
            raise ValueError(f'bundle provenance differs from manifest: {field}')
    filenames = {name + suffix for suffix in ('.raw', '.module.json', '.build-info.json')}
    filenames.add(name + '.source-' + package['source']['filename'])
    filenames.update(name + '.' + value for value in package['build'].get('artifacts', []))
    if (root / 'packages' / name / 'NOTICE').exists():
        filenames.add(name + '.NOTICE')
    if {path.name for path in directory.iterdir()} != filenames | {name + '.SHA256SUMS'}:
        raise ValueError('bundle distribution file set differs from manifest')
    checksums = {}
    for line in (directory / (name + '.SHA256SUMS')).read_text().splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9_.+-]*)', line)
        if not match or match[2] in checksums:
            raise ValueError('invalid or duplicate distribution checksum')
        checksums[match[2]] = match[1]
    if checksums.keys() != filenames:
        raise ValueError('checksums must cover every distribution file')
    for filename, checksum in checksums.items():
        if digest(directory / filename) != checksum:
            raise ValueError(f'distribution checksum mismatch: {filename}')
    source = directory / (name + '.source-' + package['source']['filename'])
    if digest(source, 'sha512') != package['source']['sha512']:
        raise ValueError('upstream source checksum mismatch')
    image = directory / (name + '.raw')
    if image_file(image, 'usr/lib/extension-release.d/extension-release.' + name).decode() != extension_release(target):
        raise ValueError('image compatibility differs from bundle provenance')
    module = read_json(directory / (name + '.module.json'))
    if module['name'] != name or module['version'] != package['module_version']:
        raise ValueError('module identity differs from manifest')
    if json.loads(image_file(image, 'usr/share/casaos/modules/' + name + '.json')) != module:
        raise ValueError('image module metadata differs from sidecar')
    if json.loads(image_file(image, 'usr/share/zimaos-packages/' + name + '/build-info.json')) != info:
        raise ValueError('image provenance differs from sidecar')
    return identity, package, checksums[name + '.raw']


def assemble(root, bundles, output, base_url):
    parsed = urlsplit(base_url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise ValueError('repository base URL must be an absolute HTTPS URL without credentials, query, or fragment')
    base_url = base_url.rstrip('/')
    config, matrix = catalog(root)
    expected = {(item['package'], item['target']) for item in matrix}
    if output.exists():
        raise ValueError('repository output must not already exist')
    output.parent.mkdir(parents=True, exist_ok=True)
    stores = {}
    records = []

    def url(path):
        return base_url + '/' + quote(path.as_posix())

    with tempfile.TemporaryDirectory(prefix='module-store-', dir=output.parent) as temporary:
        work = Path(temporary)
        site = work / 'site'
        site.mkdir()
        for bundle in sorted(bundles.rglob('*.bundle.tar.gz')):
            with tempfile.TemporaryDirectory(prefix='bundle-', dir=work) as extracted:
                directory = Path(extracted)
                unpack_bundle(bundle, directory)
                identity, package, checksum = verify_bundle(directory, root, expected)
                name, target = identity
                expected.remove(identity)
                target_info = load_target(root, target)
                platform = repository_path(target_info)
                # The bundle hash identifies the complete distribution,
                # including notices and provenance as well as the raw image.
                relative = platform / 'packages' / name / package['module_version'] / digest(bundle)
                destination = site / relative
                destination.mkdir(parents=True)
                for path in directory.iterdir():
                    shutil.copyfile(path, destination / path.name)
                shutil.copyfile(bundle, destination / bundle.name)
                entry = {'name': name, 'title': package.get('title', name),
                         'version': package['module_version'], 'url': url(relative / (name + '.raw'))}
                stores.setdefault(target, []).append(entry)
                records.append({**entry, 'target': target, 'os_id': target_info['os_id'],
                                'catalog_url': url(platform / 'mod-v2.json'),
                                'architecture': package['architecture'],
                                'sha256': checksum, 'size_bytes': (destination / (name + '.raw')).stat().st_size,
                                'bundle_url': url(relative / bundle.name),
                                'checksums_url': url(relative / (name + '.SHA256SUMS'))})
        if expected:
            raise ValueError(f'missing distribution bundles: {sorted(expected)}')
        for target, entries in stores.items():
            platform = repository_path(load_target(root, target))
            write_json(site / platform / 'mod-v2.json', entries)
        write_json(site / 'repository.json', {'base_url': base_url, 'targets': config['targets'], 'packages': records})
        (site / '.nojekyll').touch()
        for path in site.rglob('*'):
            if path.is_file() and path.stat().st_size >= MAX_FILE_BYTES:
                raise ValueError(f'file exceeds GitHub publication limit: {path.name}')
        site.rename(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('matrix', 'build'))
    parser.add_argument('--bundles', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--base-url')
    args = parser.parse_args()
    if args.action == 'matrix':
        print(json.dumps({'include': catalog(ROOT)[1]}))
    else:
        if not all((args.bundles, args.output, args.base_url)):
            parser.error('build requires --bundles, --output, and --base-url')
        assemble(ROOT, args.bundles, args.output, args.base_url)


if __name__ == '__main__':
    try:
        main()
    except (KeyError, ValueError, OSError, tarfile.TarError, subprocess.CalledProcessError) as error:
        print(f'error: {error}', file=sys.stderr)
        sys.exit(1)
