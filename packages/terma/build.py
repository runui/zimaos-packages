"""Package the upstream Web release with a private Node.js runtime."""

import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import sys
import tempfile

sys.path.insert(0, os.environ['ZPKG_TOOLS_DIR'])
from common import digest, extract_archive, pack_tree, run, write_json
from pipeline import fetch


def check_binary(path, arch, runtime):
    header = path.read_bytes()[:20]
    if (header[:6] != b'\x7fELF\x02\x01' or
            struct.unpack_from('<H', header, 18)[0] != {'x86_64': 62, 'aarch64': 183}[arch]):
        raise ValueError(f'not a Linux {arch} binary: {path.name}')
    dynamic = subprocess.check_output(['readelf', '-d', path], text=True)
    if '(RPATH)' in dynamic or '(RUNPATH)' in dynamic:
        raise ValueError(f'binary contains a build-specific library search path: {path.name}')
    versions = subprocess.check_output(['readelf', '--version-info', path], text=True)
    for prefix, maximum in [('GLIBC', runtime['glibc_minimum']),
                            ('GLIBCXX', runtime['glibcxx_maximum_required'])]:
        limit = tuple(map(int, maximum.split('.')))
        if any(tuple(map(int, value.split('.'))) > limit
               for value in re.findall(r'Name: ' + prefix + r'_([\d.]+)', versions)):
            raise ValueError(f'{path.name} requires {prefix} newer than {maximum}')


def dependency_sources(application, lock, sources):
    records = []
    for name, spec in lock['packages'].items():
        if not name or not (application / name / 'package.json').is_file():
            continue
        checksum = base64.b64decode(spec['integrity'].removeprefix('sha512-'), validate=True).hex()
        records.append({'path': name, 'version': spec['version'], 'url': spec['resolved'],
                        'sha512': checksum, 'filename': checksum + '.tgz'})

    def download(record):
        fetch(record, sources / record['filename'], None)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(download, records))
    return records


def main():
    package = json.loads(Path(os.environ['ZPKG_MANIFEST']).read_text())
    arch = os.environ['ZPKG_TARGET_ARCH']
    if platform.system() != 'Linux' or platform.machine() != arch:
        raise ValueError('build native dependencies on matching Linux hardware')
    build = Path(os.environ['ZPKG_BUILD_DIR'])
    runtime = package['runtime']
    node_source = runtime['sources'][arch]
    node_archive = build / node_source['filename']
    fetch(node_source, node_archive, None)
    with tempfile.TemporaryDirectory(dir=build) as temporary:
        work = Path(temporary)
        extract_archive(Path(os.environ['ZPKG_SOURCE_ARCHIVE']), work / 'source')
        extract_archive(node_archive, work / 'node')
        application = work / 'source/terma'
        node_root, = (work / 'node').iterdir()
        node = node_root / 'bin/node'
        check_binary(node, arch, runtime)
        if subprocess.check_output([node, '--version'], text=True).strip() != 'v' + runtime['node_version']:
            raise ValueError('unexpected Node.js version')
        if json.loads((application / 'package.json').read_text())['version'] != package['version']:
            raise ValueError('unexpected Terma release version')
        lock_path = application / 'package-lock.json'
        original_lock_sha256 = digest(lock_path)
        lock = json.loads(lock_path.read_text())
        # Use the canonical registry without changing upstream versions or SRI.
        for name, spec in lock['packages'].items():
            if not name:
                continue
            if not re.fullmatch(r'sha512-[A-Za-z0-9+/]{86}==', spec.get('integrity', '')):
                raise ValueError(f'dependency is not pinned by SHA-512: {name}')
            spec['resolved'] = spec['resolved'].replace('https://registry.npmmirror.com/',
                                                       'https://registry.npmjs.org/', 1)
            if not spec['resolved'].startswith('https://registry.npmjs.org/'):
                raise ValueError(f'unexpected dependency source: {name}')
        write_json(lock_path, lock)
        npm_root = node_root / 'lib/node_modules/npm'
        environment = {**os.environ, 'PATH': str(node_root / 'bin') + os.pathsep + os.environ['PATH'],
                       'HOME': str(work / 'home'), 'npm_config_update_notifier': 'false'}
        user_config, global_config = work / 'user.npmrc', work / 'global.npmrc'
        user_config.write_text('')
        global_config.write_text('')
        run(node, npm_root / 'bin/npm-cli.js', 'ci', '--omit=dev', '--ignore-scripts',
            '--no-audit', '--no-fund', '--registry=https://registry.npmjs.org/',
            '--userconfig=' + str(user_config), '--globalconfig=' + str(global_config),
            '--cache=' + str(build / 'npm-cache'), cwd=application, env=environment)
        modules = application / 'node_modules'
        sources = work / 'runtime-sources'
        sources.mkdir()
        records = dependency_sources(application, lock, sources)
        # Disable dependency install scripts: node-gyp uses only the headers and
        # tool bundled in the verified Node archive, without downloading prebuilds.
        for name in ('node-pty', '@serialport/bindings-cpp'):
            module = modules / name
            run(node, npm_root / 'node_modules/node-gyp/bin/node-gyp.js', 'rebuild',
                '--directory=' + str(module), '--nodedir=' + str(node_root),
                cwd=application, env=environment)
            native = module / 'build/Release'
            for path in native.iterdir():
                if path.suffix == '.node':
                    run('strip', '--strip-unneeded', path)
                    check_binary(path, arch, runtime)
                elif path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            for path in (module / 'build').iterdir():
                if path.name != 'Release':
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
        for path in modules.rglob('prebuilds'):
            if path.is_dir():
                shutil.rmtree(path)
        for name in ('patch-ssh2-sftp-raw.js', 'prepare-zmodem-runtime.js'):
            run(node, application / 'scripts' / name, cwd=application, env=environment)
        run(node, '-e', "require('node:sqlite');require('node-pty');require('serialport');require('ssh2')",
            cwd=application, env=environment)

        destination = Path(os.environ['DESTDIR']) / 'usr/lib/terma'
        destination.mkdir(parents=True)
        for name in ('dist', 'public', 'node_modules'):
            shutil.copytree(application / name, destination / name, symlinks=True)
        for name in ('package.json', 'package-lock.json'):
            shutil.copyfile(application / name, destination / name)
        private_bin = destination / 'runtime/bin'
        private_bin.mkdir(parents=True)
        shutil.copy2(node, private_bin / 'node')
        licenses = Path(os.environ['DESTDIR']) / 'usr/share/licenses/terma'
        licenses.mkdir(parents=True)
        for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
            shutil.copyfile(application / name, licenses / name)
        shutil.copyfile(node_root / 'LICENSE', licenses / 'Node.js-LICENSE')
        shutil.copyfile(application / 'public/fonts/OFL.txt', licenses / 'NotoSansSC-OFL.txt')
        for record in records:
            module = application / record['path']
            for path in module.rglob('*'):
                if (path.is_file() and not path.is_symlink() and
                        path.name.lower().startswith(('license', 'licence', 'copying', 'copyright',
                                                      'notice', 'authors', 'ofl'))):
                    target = licenses / 'third-party' / record['path'].removeprefix('node_modules/') / path.relative_to(module)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, target)
        artifacts = Path(os.environ['ZPKG_ARTIFACTS_DIR'])
        inventory = {'node': node_source, 'packages': records}
        write_json(artifacts / 'runtime-packages.json', inventory)
        write_json(licenses / 'runtime-packages.json', inventory)
        shutil.copyfile(node_archive, sources / node_source['filename'])
        pack_tree(sources, artifacts / 'runtime-sources.tar', package['source_date_epoch'])
        write_json(Path(os.environ['ZPKG_DETAILS_FILE']), {
            'upstream_revision': package['upstream_revision'],
            'precompiled_upstream_web_release': True,
            'native_dependencies_built_locally': ['node-pty', '@serialport/bindings-cpp'],
            'upstream_lock_sha256': original_lock_sha256,
            'node_version': runtime['node_version'], 'node_source': node_source,
            'node_binary_sha256': digest(node), 'runtime': runtime,
        })


if __name__ == '__main__':
    main()
