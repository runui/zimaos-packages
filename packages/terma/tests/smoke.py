#!/usr/bin/env python3
"""Exercise the extracted Web service with isolated state and local requests."""

import argparse
from contextlib import contextmanager
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


@contextmanager
def server(launcher, environment, work, port):
    with (work / 'server.log').open('a') as log:
        process = subprocess.Popen([str(launcher), '--host', '127.0.0.1', '--port', str(port)],
                                   env=environment, stdout=log, stderr=log)
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                require(process.poll() is None, (work / 'server.log').read_text())
                try:
                    with opener.open(f'http://127.0.0.1:{port}/api/auth/status', timeout=1) as response:
                        require(response.status == 200, 'auth status is unavailable')
                    break
                except (OSError, urllib.error.URLError):
                    time.sleep(0.05)
            else:
                raise RuntimeError('server did not become ready:\n' + (work / 'server.log').read_text())
            yield
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise RuntimeError('server did not stop after SIGTERM')
            require(process.returncode == 0, (work / 'server.log').read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True)
    args = parser.parse_args()
    package = json.loads((Path(__file__).resolve().parents[1] / 'package.json').read_text())
    with tempfile.TemporaryDirectory(dir=os.environ['ZPKG_TEST_WORK_DIR']) as temporary:
        work = Path(temporary)
        root = work / 'image'
        subprocess.run(['unsquashfs', '-no-progress', '-d', str(root), str(args.image)],
                       check=True, stdout=subprocess.DEVNULL)
        require([p.name for p in root.iterdir()] == ['usr'], 'payload is not confined to /usr')
        module = json.loads((root / 'usr/share/casaos/modules/terma.json').read_text())
        require(module['name'] == package['name'] and module['version'] == package['module_version'],
                'module identity does not match the recipe')
        require(module.get('ui') == package['ui'], 'dashboard declaration is missing or incorrect')
        dashboard_root = root / 'usr/share/casaos/www'
        entry = dashboard_root / module['ui']['entry'].lstrip('/')
        require(entry.is_file(), 'dashboard entry is missing')
        icon = dashboard_root / module['ui']['icon'].lstrip('/')
        require(icon.is_file(), 'dashboard icon is missing')
        application = root / 'usr/lib/terma'
        node = application / 'runtime/bin/node'
        require(icon.read_bytes() == (application / 'public/assets/terma-icon.png').read_bytes(),
                'dashboard icon differs from the upstream icon')
        subprocess.run([node, '-e', r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[1], 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
for (const origin of ['http://nas.local', 'https://192.0.2.1:8443', 'https://[2001:db8::1]']) {
  let target;
  vm.runInNewContext(script, {URL, window: {location: {
    origin, replace: value => { target = value; }
  }}});
  assert.equal(target, `http://${new URL(origin).hostname}:8088/`);
}
''', entry], check=True, timeout=10)
        service = (root / 'usr/lib/systemd/system/zimaos-terma.service').read_text()
        require('Environment=TUNNEL_WEB_HOST=0.0.0.0\n' in service,
                'managed service does not enable LAN access')
        info = json.loads((root / 'usr/share/zimaos-packages/terma/build-info.json').read_text())
        require(info['architecture'] == os.environ['ZPKG_TARGET_ARCH'], 'wrong target architecture')
        require(hashlib.sha256(node.read_bytes()).hexdigest() == info['details']['node_binary_sha256'],
                'private runtime does not match provenance')
        result = subprocess.check_output([node, '--version'], text=True).strip()
        require(result == 'v' + package['runtime']['node_version'], 'wrong Node.js runtime')
        licenses = root / 'usr/share/licenses/terma'
        for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md', 'Node.js-LICENSE', 'NotoSansSC-OFL.txt',
                     'third-party/node-pty/LICENSE', 'third-party/@novnc/novnc/LICENSE.txt',
                     'packaging/LICENSE', 'packaging/NOTICE'):
            require((licenses / name).is_file(), 'missing license: ' + name)
        inventory = json.loads((licenses / 'runtime-packages.json').read_text())
        require(any(p['path'] == 'node_modules/ssh2' for p in inventory['packages']),
                'runtime inventory is incomplete')
        require(not (root / 'usr/bin/node').exists(), 'private Node.js shadows host Node.js')
        require(not list((application / 'node_modules').rglob('prebuilds')),
                'foreign-platform prebuilds remain in the payload')
        state = work / 'state'
        state.mkdir(mode=0o700)
        home = work / 'home'
        home.mkdir(mode=0o700)
        user_ssh = home / '.ssh'
        user_ssh.mkdir(mode=0o700)
        known_hosts = user_ssh / 'known_hosts'
        known_hosts.write_text('# independently managed SSH settings\n')
        saved_known_hosts = known_hosts.read_bytes()
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(('TERMA_', 'TUNNELDESK_', 'TUNNEL_WEB_', 'NODE_', 'SSH_'))}
        environment.update(HOME=str(home), TERMA_DATA_DIR=str(state / 'data'),
                           TERMA_SSH_DIR=str(state / 'ssh'), TERMA_DISABLE_UPDATE_CHECK='1')
        # An actual shell on the compiled PTY catches missing ABI bindings and
        # runtime libraries, beyond merely requiring the optional module.
        subprocess.run([node, '-e', '''
const assert = require('node:assert/strict');
const { DatabaseSync } = require('node:sqlite');
const db = new DatabaseSync(':memory:');
assert.equal(db.prepare('SELECT 42 AS answer').get().answer, 42);
db.close();
require('serialport');
const ssh2 = require('ssh2');
const key = ssh2.utils.generateKeyPairSync('ed25519');
const sshServer = new ssh2.Server({hostKeys:[key.private]}, connection => {
  connection.on('authentication', context => {
    if (context.method === 'password' && context.username === 'tester' && context.password === 'local') {
      context.accept();
    } else context.reject();
  });
  connection.on('ready', () => connection.on('session', accept => {
    accept().on('exec', (accept, reject, info) => {
      assert.equal(info.command, 'smoke-command');
      const stream = accept();
      stream.write('terma-ssh-ready');
      stream.exit(0);
      stream.end();
    });
  }));
});
sshServer.listen(0, '127.0.0.1', () => {
  const client = new ssh2.Client();
  client.on('error', error => {throw error;});
  client.on('ready', () => client.exec('smoke-command', (error, stream) => {
    assert.ifError(error);
    let received = '';
    stream.on('data', data => received += data);
    stream.on('close', code => {
      assert.equal(code, 0);
      assert.equal(received, 'terma-ssh-ready');
      client.end();
      sshServer.close();
    });
  }));
  client.connect({host:'127.0.0.1', port:sshServer.address().port,
                  username:'tester', password:'local', readyTimeout:5000});
});
let output = '';
const terminal = require('node-pty').spawn('/bin/sh', ['-c', 'printf terma-pty-ready'],
                                         {cwd:process.env.HOME, env:process.env});
terminal.onData(data => output += data);
terminal.onExit(({exitCode}) => {
  assert.equal(exitCode, 0);
  assert.ok(output.includes('terma-pty-ready'));
});
'''], cwd=application, env=environment, check=True, timeout=20)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        cookies = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                             urllib.request.HTTPCookieProcessor(cookies))

        def request(path, method='GET', data=None, status=200, raw=False):
            body = json.dumps(data).encode() if data is not None else None
            req = urllib.request.Request(base + path, data=body, method=method,
                                         headers={'Origin': base, 'Content-Type': 'application/json'})
            try:
                response = opener.open(req, timeout=5)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                content = response.read()
                require(response.status == status,
                        f'{method} {path} returned {response.status}: {content[:500]!r}')
                return content if raw else json.loads(content)

        launcher = root / 'usr/bin/terma'
        with server(launcher, environment, work, port):
            require(b'Terma' in request('/', raw=True), 'Web page was not served')
            for path in ('/vendor/xterm/xterm.js', '/vendor/ace/ace.js',
                         '/vendor/novnc/core/rfb.js', '/vendor/zmodem.js',
                         '/vendor/i18next/i18next.min.js', '/vendor/lucide/lucide.min.js',
                         '/vendor/jspdf/jspdf.umd.min.js', '/vendor/svg2pdf/svg2pdf.umd.min.js'):
                content = request(path, raw=True)
                require(len(content) > 100 and b'<!doctype html>' not in content[:100].lower(),
                        'missing browser dependency: ' + path)
            connection = request('/api/connections', 'POST', {
                'name': 'persistent-smoke', 'ssh_host': 'example.invalid', 'ssh_port': 22,
                'ssh_user': 'tester', 'auth_type': 'key', 'identity_file': ''}, status=201)
            require(connection['id'] > 0, 'connection was not saved')
            password = 'Terma-test-' + os.urandom(16).hex()
            request('/api/security/password', 'POST', {'password': password})
            request('/api/security', 'PUT', {'auth_mode': 'always'})
            cookies.clear()
            request('/api/connections', status=401)
            request('/api/auth/login', 'POST', {'password': 'incorrect'}, status=401)
            request('/api/auth/login', 'POST', {'password': password})
            require(request('/api/connections')[0]['name'] == 'persistent-smoke',
                    'authenticated API cannot read saved connection')
        require(not (state / 'data/web.pid').exists(), 'SIGTERM left a stale PID file')
        security = (state / 'data/security.json').read_bytes()
        cookies.clear()
        with server(launcher, environment, work, port):
            request('/api/connections', status=401)
            request('/api/auth/login', 'POST', {'password': password})
            require(request('/api/connections')[0]['id'] == connection['id'],
                    'database did not survive restart')
            require((state / 'data/security.json').read_bytes() == security,
                    'restart changed saved security settings')
        for name in ('data', 'ssh'):
            require((state / name).stat().st_mode & 0o077 == 0, 'state directory is not private')
        require(known_hosts.read_bytes() == saved_known_hosts, 'server changed caller SSH files')
        print('Terma dashboard, runtime, PTY, SSH, Web assets, authentication, persistence, and shutdown passed')


if __name__ == '__main__':
    main()
