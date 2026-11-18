#!/usr/bin/env python3
"""Individual extension tasks scheduled by Meson/Ninja."""

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

from common import digest, extract_archive, pack_tree, replace_if_changed, run, write_changed, write_json
from manifest import extension_release, read_json


def fetch(spec, output, cache):
    output.parent.mkdir(parents=True, exist_ok=True)
    cached = Path(cache) / (spec['sha512'] + '-' + spec['filename']) if cache else None
    if cached and cached.exists():
        if digest(cached, 'sha512') != spec['sha512']:
            raise ValueError(f'cached source checksum mismatch: {cached}')
        write_changed(output, cached.read_bytes())
        return
    if output.is_file() and digest(output, 'sha512') == spec['sha512']:
        return
    with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            request = urllib.request.Request(spec['url'], headers={'User-Agent': 'zimaos-packages'})
            with urllib.request.urlopen(request, timeout=60) as response:
                shutil.copyfileobj(response, stream)
            stream.flush()
            if digest(temporary, 'sha512') != spec['sha512']:
                raise ValueError(f"source checksum mismatch: {spec['url']}")
            temporary.chmod(0o644)
            if cached:
                write_changed(cached, temporary.read_bytes())
            replace_if_changed(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)


def validate_root(root):
    if any(path.name != 'usr' for path in root.iterdir()) or (root / 'usr').is_symlink():
        raise ValueError('system extensions may only contain a real /usr directory')
    for path in root.rglob('*'):
        if not (path.is_symlink() or path.is_file() or path.is_dir()):
            raise ValueError(f'special file in extension payload: {path}')


def stage(spec, args):
    package = spec['package']
    if digest(args.source, 'sha512') != package['source']['sha512']:
        raise ValueError('source checksum mismatch before package execution')
    args.work_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=package['name'] + '-stage-', dir=args.output_dir) as temporary:
        work = Path(temporary)
        rootfs, artifacts = work / 'rootfs', work / 'artifacts'
        rootfs.mkdir()
        artifacts.mkdir()
        details = work / 'details.json'
        environment = {**os.environ,
                       'DESTDIR': str(rootfs), 'SOURCE_DATE_EPOCH': str(package['source_date_epoch']),
                       'ZPKG_SOURCE_ARCHIVE': str(args.source.resolve()),
                       'ZPKG_BUILD_DIR': str(args.work_dir.resolve()),
                       'ZPKG_ARTIFACTS_DIR': str(artifacts), 'ZPKG_DETAILS_FILE': str(details),
                       'ZPKG_MANIFEST': spec['manifest'], 'ZPKG_PACKAGE_DIR': spec['package_dir'],
                       'ZPKG_TARGET_NAME': spec['target_name'], 'ZPKG_TARGET_ARCH': spec['target']['architecture'],
                       'ZPKG_TARGET_JSON': json.dumps(spec['target']),
                       'ZPKG_TOOLS_DIR': str(Path(__file__).resolve().parent),
                       'PYTHONDONTWRITEBYTECODE': '1'}
        run(*package['build']['command'], cwd=spec['package_dir'], env=environment)
        validate_root(rootfs)
        for name in package['build'].get('artifacts', []):
            artifact = artifacts / name
            if not artifact.is_file() or artifact.is_symlink():
                raise ValueError(f'recipe did not produce declared artifact: {name}')
        metadata = read_json(details) if details.exists() else {}
        pack_tree(rootfs, args.archive, package['source_date_epoch'])
        write_json(args.details, metadata)
        for name in package['build'].get('artifacts', []):
            write_changed(args.output_dir / (package['name'] + '.' + name), (artifacts / name).read_bytes())
        # A successful recipe run must advance every output past its inputs,
        # even when it produced identical bytes. This also survives a Meson
        # regeneration without repeating compilation of an unchanged recipe.
        for path in [args.archive, args.details] + [args.output_dir / (package['name'] + '.' + name)
                                                  for name in package['build'].get('artifacts', [])]:
            path.touch()


def input_digest(path):
    path = Path(path)
    if path.is_symlink():
        return hashlib.sha256(('symlink:' + str(path.readlink())).encode()).hexdigest()
    return digest(path)


def assemble(spec, args):
    package = spec['package']
    directory = Path(spec['package_dir'])
    name = package['name']
    with tempfile.TemporaryDirectory(prefix=name + '-image-', dir=args.output_dir) as temporary:
        rootfs = Path(temporary) / 'rootfs'
        extract_archive(args.archive, rootfs)
        if (directory / 'files').exists():
            shutil.copytree(directory / 'files', rootfs, dirs_exist_ok=True, symlinks=True)
        validate_root(rootfs)
        metadata = rootfs / 'usr/lib/extension-release.d' / ('extension-release.' + name)
        metadata.parent.mkdir(parents=True, exist_ok=True)
        metadata.write_text(extension_release(spec['target']))
        module = {'name': name, 'version': package['module_version'],
                  'services': package.get('services', []),
                  'hook': {hook: [] for hook in ('preInstall', 'postInstall', 'preUninstall',
                                                'postUninstall', 'preUpdate', 'postUpdate')}}
        write_json(rootfs / 'usr/share/casaos/modules' / (name + '.json'), module)
        root = Path(__file__).resolve().parents[1]
        licenses = rootfs / 'usr/share/licenses' / name
        if (directory / 'licenses').exists():
            shutil.copytree(directory / 'licenses', licenses, dirs_exist_ok=True, symlinks=True)
        license_path = licenses / 'packaging/LICENSE'
        license_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / 'LICENSE', license_path)
        if (directory / 'NOTICE').exists():
            shutil.copyfile(directory / 'NOTICE', licenses / 'packaging/NOTICE')
            write_changed(args.output_dir / (name + '.NOTICE'), (directory / 'NOTICE').read_bytes())
        provenance = {'package': name, 'version': package['version'], 'release': package['release'],
                      'module_version': package['module_version'], 'target': spec['target_name'],
                      'os_id': spec['target']['os_id'],
                      'architecture': spec['target']['architecture'],
                      'source': package['source'], 'source_date_epoch': package['source_date_epoch'],
                      'manifest': package,
                      'recipe': {name: input_digest(path) for name, path in spec['inputs'].items()},
                      'details': read_json(args.details)}
        write_json(rootfs / 'usr/share/zimaos-packages' / name / 'build-info.json', provenance)
        pack_tree(rootfs, args.output_dir / (name + '.image.tar'), package['source_date_epoch'])
        write_json(args.output_dir / (name + '.module.json'), module)
        write_json(args.output_dir / (name + '.build-info.json'), provenance)


def image(spec, args):
    with tempfile.TemporaryDirectory(prefix='squashfs-', dir=args.output.parent) as temporary:
        work = Path(temporary)
        extract_archive(args.archive, work / 'rootfs')
        temporary_image = work / 'extension.raw'
        epoch = str(spec['epoch'])
        run(args.mksquashfs, work / 'rootfs', temporary_image, '-noappend', '-all-root', '-no-xattrs',
            '-comp', 'gzip', '-processors', spec['threads'], '-mkfs-time', epoch, '-all-time', epoch,
            '-no-progress', '-quiet')
        replace_if_changed(temporary_image, args.output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('fetch', 'stage', 'assemble', 'image', 'checksums', 'bundle', 'test'))
    parser.add_argument('--spec', type=Path)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--details', type=Path)
    parser.add_argument('--work-dir', type=Path)
    parser.add_argument('--cache', default='')
    parser.add_argument('--mksquashfs')
    parser.add_argument('--suite')
    parser.add_argument('--image', type=Path)
    parser.add_argument('inputs', nargs='*')
    args = parser.parse_intermixed_args()
    spec = read_json(args.spec) if args.spec else None
    if args.phase == 'fetch':
        fetch(spec, args.output, args.cache)
    elif args.phase == 'stage':
        stage(spec, args)
    elif args.phase == 'assemble':
        assemble(spec, args)
    elif args.phase == 'image':
        image(spec, args)
    elif args.phase == 'checksums':
        write_changed(args.output, ''.join(f'{digest(path)}  {Path(path).name}\n' for path in args.inputs))
    elif args.phase == 'bundle':
        with tempfile.TemporaryDirectory(prefix='release-', dir=args.output.parent) as temporary:
            work = Path(temporary)
            payload = work / 'files'
            payload.mkdir()
            for path in args.inputs:
                shutil.copyfile(path, payload / Path(path).name)
            pack_tree(payload, work / 'release.tar', spec['epoch'])
            write_changed(args.output, gzip.compress((work / 'release.tar').read_bytes(), mtime=spec['epoch']))
    else:
        args.work_dir.mkdir(parents=True, exist_ok=True)
        environment = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1',
                       'ZPKG_TARGET_NAME': spec['target_name'], 'ZPKG_TARGET_ARCH': spec['architecture'],
                       'ZPKG_TEST_WORK_DIR': str(args.work_dir.resolve())}
        run(*spec['commands'][args.suite], '--image', args.image.resolve(),
            cwd=spec['package_dir'], env=environment)


if __name__ == '__main__':
    try:
        main()
    except (KeyError, ValueError, OSError, tarfile.TarError, subprocess.CalledProcessError) as error:
        print(f'error: {error}', file=sys.stderr)
        sys.exit(1)
