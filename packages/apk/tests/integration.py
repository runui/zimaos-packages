#!/usr/bin/env python3
"""Validate the packaged Alpine environment in a disposable mount namespace."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from smoke import ROOT, digest, load_package, make_repository, official_apk, require


def child(work, image, parent_namespace):
    require(os.readlink('/proc/self/ns/mnt') != parent_namespace, 'private namespace required')
    unpacked = work / 'unpacked'
    subprocess.run(['unsquashfs', '-no-progress', '-d', unpacked, image], check=True)
    helper = unpacked / 'usr/libexec/zimaos-packages/apk-environment'
    base = unpacked / 'usr/share/zimaos-packages/apk/base'
    native = official_apk(base, work / 'official-apk')
    environment = {**os.environ,
                   'APK_ENV_STATE': str(work / 'state'),
                   'APK_ENV_RUN': str(work / 'runtime'),
                   'APK_ENV_BASE': str(base),
                   'APK_ENV_HELPER': str(helper),
                   'APK_ENV_BIN': str(work / 'bin'),
                   'APK_ENV_LAUNCH': str(unpacked / 'usr/libexec/zimaos-packages/apk-launch'),
                   'APK_ENV_VERSION': str(base.parent / 'runtime-version')}

    def run(*arguments, success=True, **kwargs):
        print('+ environment', *arguments, flush=True)
        result = subprocess.run([helper, *arguments], env=environment, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs)
        print(result.stdout, end='', flush=True)
        require((result.returncode == 0) == success, f'unexpected status {result.returncode}')
        return result

    original = digest(image)
    # Calling the internal mounting branch directly must fail before mounting.
    run('namespace', 'exec', '/bin/true', success=False)
    run('init')
    root = work / 'runtime/root'
    upper = work / 'state/upper'
    try:
        repositories = (root / 'etc/apk/repositories').read_text()
        expected = load_package(ROOT, 'apk')['runtime']['repositories']
        require(repositories.splitlines() == expected, 'configured repositories missing')
        require(list((root / 'etc/apk/keys').glob('*.pub')), 'official signing keys missing')
        run('package', 'info', '--exists', 'busybox', 'apk-tools')
        repository, public, _, _ = make_repository(native, work)
        shutil.copyfile(public, root / 'etc/apk/keys' / public.name)
        (root / 'etc/apk/repositories').write_text(str(repository / 'Packages.adb') + '\n')
        collision = work / 'bin/apk-smoke-tool'
        collision.write_text('user-owned entry\n')
        run('package', 'add', 'apk-smoke')
        require(collision.read_text() == 'user-owned entry\n', 'existing command entry was replaced')
        collision.unlink()
        run('init')
        require((upper / 'usr/share/apk-smoke/apk-smoke').read_text() == 'version one\n',
                'package payload bypassed upper')
        database = (root / 'lib/apk/db/installed').resolve().relative_to(root.resolve())
        require((upper / database).is_file(), 'database bypassed upper')
        require((work / 'bin/apk-smoke-tool').is_symlink(), 'command launcher missing')
        require(run('exec', 'apk-smoke-tool').stdout == 'fixture command works\n',
                'installed executable failed inside the environment')
        public = unpacked / 'usr/bin/apk'
        # Program flags must reach the program rather than selecting apk's
        # global --version/--help/--root options in the public wrapper.
        result = subprocess.run([public, 'exec', '/bin/sh', '-c', 'printf "%s\\n" "$@"',
                                 'sh', '--version', '--help', '--root', 'marker'],
                                env=environment, text=True, capture_output=True)
        require(result.returncode == 0 and result.stdout == '--version\n--help\n--root\nmarker\n',
                'public wrapper intercepted program options')
        result = subprocess.run([public, 'exec', '--', '/bin/sh', '-c', 'exit 37'], env=environment)
        require(result.returncode == 37, 'public exec separator/exit status failed')
        result = subprocess.run([public, '--root', root, 'info', '--exists', 'apk-smoke'],
                                env=environment, text=True, capture_output=True)
        require(result.returncode == 0 and 'apk-smoke' in result.stdout,
                'explicit --root did not retain upstream behavior')
        # Host cwd and stdin must be usable by a real Alpine executable.
        (work / 'host-file').write_text('host data\n')
        result = run('exec', '/bin/cat', 'host-file', cwd=work)
        require(result.stdout == 'host data\n', 'relative host file access failed')
        result = run('exec', '/bin/cat', input='stdin data\n')
        require(result.stdout == 'stdin data\n', 'stdin was not preserved')
        result = run('exec', '/bin/sh', '-c', 'exit 37', success=False)
        require(result.returncode == 37, 'command exit status was not preserved')
        # Transient bind mounts must vanish with the command namespace.
        mounts = Path('/proc/self/mountinfo').read_text().splitlines()
        require(not any(line.split()[4].startswith(str(root) + '/') for line in mounts),
                'a command leaked host bind mounts into the persistent root')
        run('stop')
        run('init')
        run('package', 'info', '--exists', 'apk-smoke')
        require((root / 'usr/share/apk-smoke/apk-smoke').read_text() == 'version one\n',
                'installed payload was lost after remount')
        run('package', 'del', 'apk-smoke')
        require(not (root / 'usr/share/apk-smoke/apk-smoke').exists(), 'uninstall left payload')
        require(not (work / 'bin/apk-smoke-tool').is_symlink(), 'uninstall left command launcher')
        require(digest(image) == original, 'raw was modified')
        (root / 'etc/apk/repositories').write_text(repositories)
    finally:
        run('stop')
    print('PASS: official base/keys/repositories, persistent OverlayFS payload/database, '
          'cwd/stdin, namespace mount cleanup, remount and uninstall', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True, type=Path)
    parser.add_argument('--child', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--parent-namespace', help=argparse.SUPPRESS)
    args = parser.parse_args()
    require(os.geteuid() == 0, 'integration tests require root')
    if args.child:
        require(bool(args.parent_namespace), 'missing parent namespace identity')
        child(args.child, args.image, args.parent_namespace)
        return
    for tool in ('unshare', 'mount', 'umount', 'findmnt', 'unsquashfs', 'ssh-keygen'):
        require(shutil.which(tool), f'missing test dependency: {tool}')

    def host_mounts():
        targets = {'/', '/usr', '/etc', '/opt', '/DATA', '/var/lib/extensions'}
        return [line for line in Path('/proc/self/mountinfo').read_text().splitlines()
                if line.split()[4] in targets]

    before = host_mounts()
    test_work = Path(os.environ['ZPKG_TEST_WORK_DIR'])
    test_work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='apk-integration-', dir=test_work) as temporary:
        subprocess.run(['unshare', '--mount', '--propagation', 'private', sys.executable,
                        Path(__file__).resolve(), '--image', args.image.resolve(),
                        '--child', temporary, '--parent-namespace', os.readlink('/proc/self/ns/mnt')],
                       check=True)
    require(host_mounts() == before, 'host system mounts changed')


if __name__ == '__main__':
    main()
