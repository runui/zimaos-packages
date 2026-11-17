#!/usr/bin/env python3
"""Expose manifest data to Meson; no build tasks are executed here."""

import argparse
import json
from pathlib import Path
import sys

from manifest import ROOT, extension_release, load_package, load_target, payload_files


def specification(package, target, args):
    name = package['name']
    directory = ROOT / 'packages' / name
    if args.phase == 'fetch':
        return package['source']
    if args.phase == 'stage':
        return {'package': {key: value for key, value in package.items() if key != 'tests'},
                'target': target, 'target_name': args.target,
                'manifest': str(directory / 'package.json'), 'package_dir': str(directory)}
    if args.phase == 'test':
        return {'commands': package.get('tests', {}), 'package_dir': str(directory)}
    if args.phase == 'image':
        return {'epoch': package['source_date_epoch'], 'threads': args.threads}
    inputs = [directory / 'package.json', ROOT / 'LICENSE', ROOT / 'meson.build', ROOT / 'meson.options']
    inputs += [directory / value for value in package['build']['inputs']]
    inputs += payload_files(directory)
    inputs += sorted((ROOT / 'tools').glob('*.py'))
    if (directory / 'NOTICE').exists():
        inputs.append(directory / 'NOTICE')
    return {'package': package, 'target': target, 'target_name': args.target,
            'os_version': args.os_version, 'package_dir': str(directory),
            'inputs': {str(path.relative_to(ROOT)): str(path) for path in inputs}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('list', 'inspect', 'spec'))
    parser.add_argument('--package')
    parser.add_argument('--target')
    parser.add_argument('--os-version')
    parser.add_argument('--phase', choices=('fetch', 'stage', 'assemble', 'image', 'test'))
    parser.add_argument('--threads', type=int, default=2)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    if args.action == 'list':
        packages = [load_package(ROOT, path.parent.name) for path in sorted((ROOT / 'packages').glob('*/package.json'))]
        if args.json:
            print(json.dumps({'include': [{'package': p['name'], 'target': t}
                                          for p in packages for t in p['targets']]}))
        else:
            print('\n'.join(package['name'] for package in packages))
        return
    package = load_package(ROOT, args.package or '')
    target = load_target(ROOT, args.target or '')
    extension_release(target, args.os_version or '')
    if args.target not in package['targets'] or package['architecture'] != target['architecture']:
        raise ValueError('package does not support the selected target')
    if args.action == 'inspect':
        directory = ROOT / 'packages' / package['name']
        print('source\t' + package['source']['filename'])
        for value in package['build']['inputs']:
            print('recipe\t' + str((directory / value).relative_to(ROOT)))
        for path in payload_files(directory):
            print('payload\t' + str(path.relative_to(ROOT)))
        for value in package['build'].get('artifacts', []):
            print('artifact\t' + value)
        for value in package.get('tests', {}):
            print('test\t' + value)
        if (directory / 'NOTICE').exists():
            print('notice\t' + str((directory / 'NOTICE').relative_to(ROOT)))
    else:
        if not args.phase:
            parser.error('spec requires --phase')
        print(json.dumps(specification(package, target, args), indent=2, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (KeyError, ValueError, OSError) as error:
        print(f'error: {error}', file=sys.stderr)
        sys.exit(1)
