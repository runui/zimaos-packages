"""Prepare the official Alpine userspace and persistent package environment."""

import json
import os
from pathlib import Path
import sys

sys.path.insert(0, os.environ['ZPKG_TOOLS_DIR'])
from common import extract_archive, write_json


def installed_packages(base):
    packages = {}
    for stanza in (base / 'lib/apk/db/installed').read_text().split('\n\n'):
        fields = dict(line.split(':', 1) for line in stanza.splitlines() if ':' in line)
        if 'P' in fields and 'V' in fields:
            packages[fields['P']] = fields['V']
    return dict(sorted(packages.items()))


def main():
    package = json.loads(Path(os.environ['ZPKG_MANIFEST']).read_text())
    runtime = package['runtime']
    share = Path(os.environ['DESTDIR']) / 'usr/share/zimaos-packages' / package['name']
    base = share / 'base'
    extract_archive(Path(os.environ['ZPKG_SOURCE_ARCHIVE']), base, exclude_trees=('dev', 'proc', 'sys', 'run'))
    for name in ('DATA', 'media', 'work', 'proc', 'sys', 'dev', 'tmp', 'root', 'home'):
        (base / name).mkdir(exist_ok=True)
    (base / 'etc/apk/repositories').write_text('\n'.join(runtime['repositories']) + '\n')
    inventory = installed_packages(base)
    if inventory.get('apk-tools') != runtime['bootstrap_apk_version']:
        raise ValueError('official rootfs apk version does not match package definition')
    (share / 'runtime-version').write_text(package['module_version'] + '\n')
    (Path(os.environ['ZPKG_ARTIFACTS_DIR']) / 'runtime-packages.txt').write_text(
        ''.join(f'{name}={version}\n' for name, version in inventory.items()))
    write_json(Path(os.environ['ZPKG_DETAILS_FILE']), {'bootstrap_apk_version': inventory['apk-tools'],
            'repositories': runtime['repositories'], 'runtime_packages': inventory,
            'compiled_locally': False})


if __name__ == '__main__':
    main()
