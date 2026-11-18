#!/usr/bin/env python3
"""Validate the packaged Alpine environment in a disposable mount namespace."""

import argparse
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile

from smoke import ROOT, digest, load_package, make_repository, official_apk, require


def check_host_home(work, run):
    # A home directory can itself be a mount. Its files and working path must
    # remain visible, including on systems where homes use separate volumes.
    host_home = work / 'host-home'
    host_home.mkdir()
    work_in_home = work.is_relative_to('/home')
    home = host_home
    if not work_in_home:
        # Supply a writable home on read-only hosts. When the workspace is
        # already below /home, keep it visible and mount only its test child.
        subprocess.run(['mount', '--bind', host_home, '/home'], check=True)
        home = Path('/home') / work.name
        home.mkdir()
    subprocess.run(['mount', '-t', 'tmpfs', 'tmpfs', home], check=True)
    try:
        (home / 'marker').write_text('host home\n')
        require(run('exec', '/bin/cat', 'marker', cwd=home).stdout == 'host home\n',
                'nested home mount was not shared')
        require(run('exec', '/bin/pwd', cwd=home).stdout.strip() == str(home),
                'nested home working directory changed its absolute path')
    finally:
        subprocess.run(['umount', home], check=True)
        if not work_in_home:
            subprocess.run(['umount', '/home'], check=True)


def check_host_runtime(helper, environment, root, run):
    # A separate runtime filesystem exercises recursive mounts, as used by
    # per-user services. No installed host daemon or client is required.
    user_runtime = Path('/run/user/1000')
    user_runtime.mkdir(parents=True)
    subprocess.run(['mount', '-t', 'tmpfs', 'tmpfs', user_runtime], check=True)
    (user_runtime / 'marker').write_text('host runtime\n')
    require(run('exec', '/bin/cat', '/run/user/1000/marker').stdout == 'host runtime\n',
            'nested host runtime mount was not shared')
    run('exec', '/bin/sh', '-c',
        'test ! -e "$1/etc/os-release" && test ! -e "$2/etc/os-release" && '
        'test ! -e /run/apk-smoke.sock', 'sh', str(root), str(root.parent / 'lower'))

    session_environment = {**environment, 'XDG_RUNTIME_DIR': str(user_runtime),
                           'SSH_AUTH_SOCK': str(user_runtime / 'apk-smoke.sock')}
    with subprocess.Popen([helper, 'exec', '/bin/sh', '-c',
                           'printf "%s\\n" "$$" "$XDG_RUNTIME_DIR" "$SSH_AUTH_SOCK"; read -r finish'],
                          env=session_environment, text=True, stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE) as session:
        try:
            pid = int(session.stdout.readline())
            require(session.stdout.readline().strip() == str(user_runtime), 'runtime variable changed')
            require(session.stdout.readline().strip() == session_environment['SSH_AUTH_SOCK'],
                    'socket variable changed')
            # /proc/PID/root exposes the launched command's filesystem view.
            # Connect through it to test real IPC without installing a client
            # in Alpine. Recreating an endpoint also exercises service restart.
            for endpoint in (Path('/run/apk-smoke.sock'), user_runtime / 'apk-smoke.sock',
                             user_runtime / 'apk-smoke.sock'):
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                    server.settimeout(5)
                    server.bind(str(endpoint))
                    server.listen(1)
                    try:
                        for visible_path in (str(endpoint), '/var' + str(endpoint)):
                            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                                client.settimeout(5)
                                client.connect(f'/proc/{pid}/root{visible_path}')
                                client.sendall(b'ping')
                                connection, _ = server.accept()
                                with connection:
                                    connection.settimeout(5)
                                    require(connection.recv(4) == b'ping', 'runtime request failed')
                                    connection.sendall(b'pong')
                                require(client.recv(4) == b'pong', 'runtime response failed')
                    finally:
                        endpoint.unlink()
        finally:
            _, errors = session.communicate(input='done\n', timeout=10)
            require(session.returncode == 0, f'runtime session failed: {errors}')
    run('exec', '/bin/sh', '-c', 'test ! -e "$1" && test ! -e "$2"', 'sh',
        '/run/apk-smoke.sock', str(user_runtime / 'apk-smoke.sock'))
    mounts = Path('/proc/self/mountinfo').read_text().splitlines()
    for target in (root, root.parent / 'lower'):
        require(not any(line.split()[4] == str(target) and 'unbindable' in line.split()[6:]
                        for line in mounts), 'command changed parent mount propagation')


def child(work, image, parent_namespace):
    require(os.readlink('/proc/self/ns/mnt') != parent_namespace, 'private namespace required')
    unpacked = work / 'unpacked'
    subprocess.run(['unsquashfs', '-no-progress', '-d', unpacked, image], check=True)
    helper = unpacked / 'usr/libexec/zimaos-packages/apk-environment'
    base = unpacked / 'usr/share/zimaos-packages/apk/base'
    native = official_apk(base, work / 'official-apk')
    # Replace /run only in this test namespace, hiding all live host services.
    # Put the environment below it to cover recursive self-mount prevention.
    host_run = work / 'host-run'
    host_run.mkdir()
    # Keep the resolver usable on hosts whose /etc/resolv.conf points into
    # /run. Reproduce its first runtime path before hiding the host directory.
    resolver = Path('/etc/resolv.conf')
    while resolver.is_symlink():
        resolver = Path(os.path.abspath(resolver.parent / resolver.readlink()))
        resolver = resolver.parent.resolve() / resolver.name
        if resolver.is_relative_to('/run'):
            copied_resolver = host_run / resolver.relative_to('/run')
            copied_resolver.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile('/etc/resolv.conf', copied_resolver)
            break
    subprocess.run(['mount', '--bind', host_run, '/run'], check=True)
    environment = {**os.environ,
                   'APK_ENV_STATE': str(work / 'state'),
                   'APK_ENV_RUN': f'/run/{work.name}/environment',
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
    root = Path(environment['APK_ENV_RUN']) / 'root'
    upper = work / 'state/upper'
    try:
        check_host_home(work, run)
        check_host_runtime(helper, environment, root, run)
        repositories = (root / 'etc/apk/repositories').read_text()
        expected = load_package(ROOT, 'apk', os.environ['ZPKG_TARGET_ARCH'])['runtime']['repositories']
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
        require(run('exec', '/bin/pwd', cwd=work).stdout.strip() == str(work.resolve()),
                'shared host working directory changed its absolute path')
        # Unshared paths remain accessible through /work without replacing
        # Alpine system directories such as /etc.
        require(run('exec', '/bin/pwd', cwd='/etc').stdout.strip() == '/work',
                'unshared working directory fallback failed')
        run('exec', '/bin/sh', '-c', 'test -f /etc/alpine-release', cwd='/etc')
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
          'host runtime IPC/restarts, cwd/stdin, namespace mount cleanup, remount and uninstall', flush=True)


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
        targets = {'/', '/usr', '/etc', '/opt', '/run', '/DATA', '/var/lib/extensions'}
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
