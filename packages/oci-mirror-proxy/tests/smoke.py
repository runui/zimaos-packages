#!/usr/bin/env python3
"""Exercise the release and lifecycle against disposable local services."""

import argparse
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'tools'))
from common import digest
from manifest import load_package


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(*args, env=None, success=True):
    result = subprocess.run(list(map(str, args)), env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
    if (result.returncode == 0) != success:
        raise RuntimeError(f'{args} exited {result.returncode}:\n{result.stdout}')
    return result.stdout


@contextmanager
def serving(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


class QuietHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, body, kind='application/json'):
        self.send_response(200)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class RegistryHandler(QuietHandler):
    def do_GET(self):
        base = f'http://127.0.0.1:{self.server.server_port}'
        if self.path == '/catalog':
            mirrors = [{'name': region, 'regions': [region], 'endpoints': [
                {'registry': 'docker.io', 'url': base + '/' + region}]} for region in ('global', 'eu')]
            self.respond(json.dumps({'version': 1, 'regions': {'global': 'Global', 'eu': 'Europe'},
                                     'mirrors': mirrors}).encode())
        elif '/manifests/' in self.path:
            self.respond(b'{"schemaVersion":2}', 'application/vnd.oci.image.manifest.v1+json')
        elif '/blobs/' in self.path:
            self.respond(b'local layer content', 'application/octet-stream')
        else:
            self.respond(b'{}')


class DockerServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class DockerHandler(QuietHandler):
    def do_GET(self):
        if self.path != '/info':
            self.send_error(404)
            return
        try:
            mirrors = json.loads(self.server.daemon.read_text()).get('registry-mirrors', [])
        except json.JSONDecodeError:
            mirrors = []
        driver = 'io.containerd.snapshotter.v1' if self.server.store == 'containerd' else 'overlay2'
        self.respond(json.dumps({'OperatingSystem': 'ZimaOS', 'OSType': 'linux',
                                'DriverStatus': [['driver-type', driver]],
                                'RegistryConfig': {'Mirrors': mirrors}}).encode())


@contextmanager
def proxy(binary, config, endpoint, env, work):
    with (work / 'proxy.log').open('a') as log:
        process = subprocess.Popen([str(binary), 'serve', '--config', str(config),
                                    '--admin-socket', endpoint], env=env, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                ready = subprocess.run([str(binary), 'status', '--admin-socket', endpoint],
                                       env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       timeout=5)
                if ready.returncode == 0:
                    break
                require(process.poll() is None, (work / 'proxy.log').read_text())
                time.sleep(0.05)
            else:
                raise RuntimeError('disposable proxy did not become ready')
            yield
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def exercise(binary, lifecycle, work, store, catalog):
    work.mkdir()
    # A procfs alias keeps Unix socket paths within Linux's length limit even
    # when the contributor's build directory has a long absolute path.
    descriptor = os.open(work, os.O_RDONLY | os.O_DIRECTORY)
    try:
        short = f'/proc/{os.getpid()}/fd/{descriptor}'
        endpoint = short + '/admin.sock'
        docker_socket = short + '/docker.sock'
        state = work / 'state'
        config = state / 'config.yaml'
        daemon = work / 'docker/daemon.json'
        daemon.parent.mkdir()
        daemon.write_text('{"debug":true,"registry-mirrors":["https://other.example"]}\n')
        hosts = work / 'docker/certs.d/docker.io/hosts.toml'
        hosts.parent.mkdir(parents=True)
        original_hosts = ('# existing configuration\nserver = "https://registry-1.docker.io"\n'
                          '[host."https://other.example"]\n  capabilities = ["pull"]\n')
        hosts.write_text(original_hosts)
        # Only the bootstrap URL is redirected. All initialization, subscriptions,
        # rendering, verification and cleanup still execute the packaged binary.
        wrapper = work / 'client'
        wrapper.write_text(f'#!{sys.executable}\n' +
                           'import os, sys\n'
                           'args = sys.argv[1:]\n'
                           'if args[:2] == ["remote", "update"]:\n'
                           '    if os.environ.get("PROXY_TEST_REMOTE_FAILURE"):\n'
                           '        sys.exit(1)\n'
                           '    if "--all" not in args:\n'
                           '        args += ["--url", os.environ["PROXY_TEST_CATALOG"]]\n'
                           'binary = os.environ["PROXY_TEST_BINARY"]\n'
                           'os.execv(binary, [binary, *args])\n')
        wrapper.chmod(0o755)
        env = {key: value for key, value in os.environ.items()
               if 'proxy' not in key.lower() and not key.startswith(('DOCKER_', 'XDG_'))}
        env.update(OCI_MIRROR_PROXY_BINARY=str(wrapper), OCI_MIRROR_PROXY_CONFIG_HOME=str(state),
                   OCI_MIRROR_PROXY_SOCKET=endpoint, PROXY_TEST_BINARY=str(binary),
                   PROXY_TEST_CATALOG=catalog, DOCKER_HOST='unix://' + docker_socket)
        options = ['--docker-socket', docker_socket, '--daemon-json', str(daemon),
                   '--certs-dir', str(hosts.parents[1]), '--pid-file', str(work / 'unused.pid'),
                   '--no-reload']

        def command(*args, **kwargs):
            return run(binary, *args, '--admin-socket', endpoint, env=env, **kwargs)

        run(lifecycle, 'prepare', env=env)
        require((state / '.bootstrap-remote').is_file(), 'fresh configuration did not request a catalog')
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        config.write_text(config.read_text().replace('127.0.0.1:24817', f'127.0.0.1:{port}'))
        saved = config.read_bytes()
        run(lifecycle, 'prepare', env=env)
        require(config.read_bytes() == saved, 'initialization overwrote existing configuration')

        docker = DockerServer(docker_socket, DockerHandler)
        docker.store, docker.daemon = store, daemon
        with serving(docker):
            with proxy(binary, config, endpoint, env, work):
                run(lifecycle, 'prepare', env=env, success=False)
                # Failed bootstrap must retain the retry marker and keep Docker
                # integration usable; the next connection retries the download.
                failed_env = {**env, 'PROXY_TEST_REMOTE_FAILURE': '1'}
                output = run(lifecycle, 'connect', *options, env=failed_env)
                require('initial mirror subscription failed' in output and
                        (state / '.bootstrap-remote').exists(), 'bootstrap failure lost its retry')
                run(lifecycle, 'connect', *options, env=env)
                require(not (state / '.bootstrap-remote').exists(), 'successful bootstrap retained its retry')
                require('/global' in command('upstream', 'list'), 'default catalog was not loaded')
                address = f'http://127.0.0.1:{port}'
                if store == 'legacy':
                    merged = json.loads(daemon.read_text())
                    require(merged['debug'] and merged['registry-mirrors'] ==
                            [address, 'https://other.example'], 'legacy Docker settings were not preserved')
                else:
                    require(address in hosts.read_text() and original_hosts.splitlines()[0] in hosts.read_text()
                            and 'https://other.example' in hosts.read_text(), 'containerd settings were not preserved')
                backups = {path: path.read_bytes() for path in (state / 'backups').glob('*')}
                run(lifecycle, 'connect', *options, env=env)
                require(backups == {path: path.read_bytes() for path in (state / 'backups').glob('*')},
                        'unchanged connection rewrote Docker configuration or backups')
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                manifest = opener.open(address + '/v2/library/test/manifests/latest', timeout=5).read()
                require(manifest == b'{"schemaVersion":2}', 'local registry manifest was not proxied')
                layer = b'local layer content'
                blob = opener.open(address + '/v2/library/test/blobs/sha256:' +
                                   hashlib.sha256(layer).hexdigest(), timeout=5).read()
                require(blob == layer, 'local registry layer was not proxied')

                config.write_bytes(saved + b'unknown_package_test_key: true\n')
                command('reload', success=False)
                require(opener.open(address + '/v2/', timeout=5).status == 200,
                        'invalid reload stopped the running service')
                config.write_bytes(saved)
                command('reload')
                command('remote', 'update', '--url', catalog, '--regions', 'eu')
                mirror = catalog.removesuffix('/catalog') + '/eu'
                command('upstream', 'disable', '--url', mirror)
                manual = catalog.removesuffix('/catalog') + '/manual'
                command('upstream', 'add', '--url', manual)
                run(lifecycle, 'connect', *options, env=env)
                listed = command('upstream', 'list')
                require(mirror in listed and 'disabled' in listed and manual in listed and '/global' not in listed,
                        'refresh lost saved regions, disabled choices, or manual upstreams')
                output = run(lifecycle, 'connect', *options, env=failed_env)
                require('keeping the saved configuration' in output and mirror in command('upstream', 'list'),
                        'failed refresh discarded saved subscriptions')
                command('remote', 'remove', '--url', catalog)
                run(lifecycle, 'connect', *options, env=env)
                require(mirror not in command('upstream', 'list') and manual in command('upstream', 'list'),
                        'removed subscription was enrolled again')
                target = daemon if store == 'legacy' else hosts
                valid = target.read_bytes()
                target.write_text('{broken' if store == 'legacy' else '[broken')
                output = run(lifecycle, 'connect', *options, env=env)
                require('Docker integration is incomplete' in output and
                        target.read_text().endswith('broken'), 'failed Docker setup was not reported safely')
                target.write_bytes(valid)
                require(config.read_bytes() != saved, 'manual upstream was not saved')
                saved = config.read_bytes()

            # Cleanup runs after process exit, like ExecStopPost. Invalid current
            # files must fail safely and keep the upstream snapshot for recovery.
            target = daemon if store == 'legacy' else hosts
            valid = target.read_bytes()
            target.write_text('{broken' if store == 'legacy' else '[broken')
            run(lifecycle, 'disconnect', *options, env=env, success=False)
            require((state / 'integration.json').exists() and target.read_text().endswith('broken'),
                    'failed cleanup discarded its recovery snapshot or changed an invalid file')
            recovery = (state / 'integration.json').read_bytes()
            run(lifecycle, 'prepare', *options, env=env, success=False)
            require((state / 'integration.json').read_bytes() == recovery,
                    'restart overwrote a snapshot still needed for recovery')
            target.write_bytes(valid)
            if store == 'legacy':
                edited = json.loads(daemon.read_text())
                edited['log-level'] = 'warn'
                daemon.write_text(json.dumps(edited))
            else:
                hosts.write_text(hosts.read_text() + '# later user edit\n')
            run(lifecycle, 'prepare', *options, env=env)
            require(not (state / 'integration.json').exists(), 'successful cleanup retained its snapshot')
            if store == 'legacy':
                removed = json.loads(daemon.read_text())
                require(removed['debug'] and removed['log-level'] == 'warn' and
                        removed['registry-mirrors'] == ['https://other.example'], 'legacy cleanup lost user edits')
            else:
                require(address not in hosts.read_text() and 'https://other.example' in hosts.read_text()
                        and '# later user edit' in hosts.read_text(), 'containerd cleanup lost user edits')
            run(lifecycle, 'disconnect', *options, env=env)
            run(lifecycle, 'prepare', env=env)
            require(config.read_bytes() == saved and not (state / '.bootstrap-remote').exists(),
                    'reinstall replaced persistent configuration or reset subscriptions')
            with proxy(binary, config, endpoint, env, work):
                run(lifecycle, 'connect', *options, env=env)
                require(manual in command('upstream', 'list') and mirror not in command('upstream', 'list'),
                        'service restart did not reuse the persisted upstream choices')
            run(lifecycle, 'disconnect', *options, env=env)
    finally:
        os.close(descriptor)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True, type=Path)
    args = parser.parse_args()
    package = load_package(ROOT, 'oci-mirror-proxy', os.environ['ZPKG_TARGET_ARCH'])
    test_work = Path(os.environ['ZPKG_TEST_WORK_DIR'])
    test_work.mkdir(parents=True, exist_ok=True)
    original = digest(args.image)
    with tempfile.TemporaryDirectory(prefix='oci-mirror-proxy-', dir=test_work) as temporary:
        work = Path(temporary)
        unpacked = work / 'image'
        run('unsquashfs', '-no-progress', '-d', unpacked, args.image)
        module = json.loads((unpacked / 'usr/share/casaos/modules/oci-mirror-proxy.json').read_text())
        require(module['name'] == package['name'] and module['version'] == package['module_version']
                and module['services'] == package['services'], 'module metadata does not match the manifest')
        binary = unpacked / 'usr/bin/oci-mirror-proxy'
        info = json.loads((unpacked / 'usr/share/zimaos-packages/oci-mirror-proxy/build-info.json').read_text())
        require(digest(binary) == info['details']['binary_sha256'], 'release binary changed during assembly')
        header = run('readelf', '-h', binary)
        require({'x86_64': 'X86-64', 'aarch64': 'AArch64'}[package['architecture']] in header,
                'unexpected executable architecture')
        require('INTERP' not in run('readelf', '-l', binary), 'release requires a host dynamic loader')
        require(run(binary, 'version').strip() == 'v' + package['version'], 'unexpected upstream version')
        require('dockerd' in run(binary, '--help'), 'upstream command interface is missing')
        for name in ('LICENSE', 'packaging/NOTICE', 'packaging/LICENSE', 'third-party/go/LICENSE',
                     'third-party/go-toml/LICENSE', 'third-party/cobra/LICENSE.txt',
                     'third-party/pflag/LICENSE', 'third-party/yaml/LICENSE', 'third-party/yaml/NOTICE'):
            require((unpacked / 'usr/share/licenses/oci-mirror-proxy' / name).is_file(), f'missing {name}')
        lifecycle = unpacked / 'usr/libexec/zimaos-packages/oci-mirror-proxy-lifecycle'
        with serving(ThreadingHTTPServer(('127.0.0.1', 0), RegistryHandler)) as registry:
            catalog = f'http://127.0.0.1:{registry.server_port}/catalog'
            for store in ('legacy', 'containerd'):
                exercise(binary, lifecycle, work / store, store, catalog)
    require(digest(args.image) == original, 'smoke test changed the image')
    print('PASS: release identity, first-run setup, subscriptions, proxy requests, both Docker stores, '
          'configuration preservation, cleanup after exit, failure recovery, and persistent reuse')


if __name__ == '__main__':
    main()
