#!/usr/bin/env python3
"""Exercise the built apk and raw image using signed, disposable packages."""

import argparse
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'tools'))

from manifest import load_package
from common import digest


def run(*arguments, expect_success=True, **kwargs):
    command = [str(argument) for argument in arguments]
    print("+", " ".join(command), flush=True)
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            env={**os.environ, "APK_CONFIG": "/dev/null"}, **kwargs)
    print(result.stdout, end="", flush=True)
    if expect_success and result.returncode:
        raise RuntimeError(f"command exited {result.returncode}: {command}")
    if not expect_success and not result.returncode:
        raise RuntimeError(f"command unexpectedly succeeded: {command}")
    return result


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)


def official_apk(base, path):
    # Test the unmodified official client on a glibc host using its own loader.
    # This adapter is disposable test tooling, not part of the raw image.
    command = [base / 'lib/ld-musl-x86_64.so.1', '--library-path',
               str(base / 'lib') + ':' + str(base / 'usr/lib'), base / 'sbin/apk']
    path.write_text('#!/bin/sh\nexec ' + ' '.join(shlex.quote(str(x)) for x in command) + ' "$@"\n')
    path.chmod(0o755)
    return path


def make_repository(apk, work):
    repository = work / "repository"
    repository.mkdir()
    key = work / "test.rsa"
    public_key = work / "test.rsa.pub"
    run("ssh-keygen", "-q", "-t", "rsa", "-b", "2048", "-m", "PEM", "-N", "", "-f", key)
    # apk/OpenSSL consumes a PEM SubjectPublicKeyInfo key, rather than the
    # OpenSSH public-key format that ssh-keygen initially writes.
    exported = run("ssh-keygen", "-e", "-m", "PKCS8", "-f", public_key)
    public_key.write_text(exported.stdout)
    signing_keys = work / "signing-keys"
    signing_keys.mkdir()
    shutil.copyfile(public_key, signing_keys / public_key.name)

    def make_package(name, version, contents, dependency=None, compression=None):
        payload = work / f"payload-{name}-{version}"
        write(payload / "usr/share/apk-smoke" / name, contents)
        if name == "apk-smoke":
            write(payload / "etc/apk-smoke.conf", "default=" + version + "\n")
            tool = payload / "usr/bin/apk-smoke-tool"
            write(tool, "#!/bin/sh\nprintf 'fixture command works\\n'\n")
            tool.chmod(0o755)
        package = repository / f"{name}-{version}.apk"
        command = [apk, "mkpkg", "--sign-key", key, "--info", f"name:{name}",
                   "--info", f"version:{version}", "--info", "arch:noarch", "--info", "license:MIT",
                   "--files", payload, "--output", package]
        if dependency:
            command.extend(["--info", f"depends:{dependency}"])
        if compression:
            command.extend(["--compression", compression])
        run(*command)
        return package

    dependency = make_package("apk-smoke-dependency", "1.0-r0", "dependency\n")
    application = make_package("apk-smoke", "1.0-r0", "version one\n", "apk-smoke-dependency=1.0-r0")

    def index(app):
        run(apk, "mkndx", "--keys-dir", signing_keys, "--sign-key", key,
            "--output", repository / "Packages.adb", dependency, app)

    index(application)
    return repository, public_key, make_package, index


def exercise_packages(apk, root, work):
    repository, public_key, make_package, index = make_repository(apk, work)
    keys = root / "etc/apk/keys"
    keys.mkdir(parents=True, exist_ok=True)
    write(root / "etc/apk/repositories", str(repository / "Packages.adb") + "\n")
    base = [apk, "--root", root, "--arch", "x86_64", "--no-progress"]
    create_options = ["--usermode"] if os.geteuid() != 0 else []

    # Verify that a repository signed by an unknown key is rejected, then trust
    # only the short-lived test key. Never use --allow-untrusted in this suite.
    rejected = run(*base, "--initdb", "add", *create_options, "apk-smoke", expect_success=False)
    require("signature" in rejected.stdout.lower(), "untrusted repository did not fail on its signature")
    shutil.copyfile(public_key, keys / public_key.name)
    # A simulation opens an existing database read-only; --initdb does not
    # create one while simulating. Initialize an empty world first.
    run(*base, "--initdb", "add", *create_options)
    run(*base, "add", "--simulate", "apk-smoke")
    installed = root / "usr/share/apk-smoke/apk-smoke"
    dependency = root / "usr/share/apk-smoke/apk-smoke-dependency"
    require(not installed.exists(), "simulation changed the target root")

    run(*base, "add", "apk-smoke")
    require(installed.read_text() == "version one\n", "application payload missing")
    require(dependency.read_text() == "dependency\n", "dependency was not installed")
    require((root / "etc/apk/world").read_text().strip() == "apk-smoke", "world contains an unexpected constraint")
    run(*base, "info", "--exists", "apk-smoke-dependency")

    # Upgrading a package must keep a locally edited /etc configuration and
    # offer the new default in .apk-new instead of silently overwriting it.
    config = root / "etc/apk-smoke.conf"
    config.write_text("user configuration\n")
    replacement = make_package("apk-smoke", "1.1-r0", "version two\n",
                               "apk-smoke-dependency=1.0-r0", compression="none")
    index(replacement)
    run(*base, "--update-cache", "upgrade", "apk-smoke")
    require(installed.read_text() == "version two\n", "upgrade did not replace the payload")
    require(config.read_text() == "user configuration\n", "upgrade overwrote the user configuration")
    require(config.with_suffix(".conf.apk-new").read_text() == "default=1.1-r0\n", "new default configuration missing")

    run(*base, "verify", replacement)
    damaged = work / "damaged.apk"
    contents = bytearray(replacement.read_bytes())
    # Flip a real payload byte in an uncompressed package. A compressed
    # stream's final byte can contain unused padding bits, so changing it does
    # not reliably corrupt any signed content. Initial packages above still
    # exercise the default compression path.
    payload_offset = contents.rfind(b"version two\n")
    require(payload_offset >= 0, "uncompressed fixture payload was not found")
    contents[payload_offset] ^= 0xFF
    damaged.write_bytes(contents)
    run(*base, "verify", damaged, expect_success=False)
    run(*base, "del", "apk-smoke")
    require(not installed.exists() and not dependency.exists(), "uninstall left the application or automatic dependency")
    require(config.read_text() == "user configuration\n", "uninstall removed the user configuration")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True, type=Path)
    args = parser.parse_args()
    package = load_package(ROOT, 'apk')
    for tool in ('unsquashfs', 'readelf', 'ssh-keygen'):
        require(shutil.which(tool), f'missing test dependency: {tool}')
    test_work = Path(os.environ['ZPKG_TEST_WORK_DIR'])
    test_work.mkdir(parents=True, exist_ok=True)
    original = digest(args.image)
    with tempfile.TemporaryDirectory(prefix='apk-smoke-', dir=test_work) as temporary:
        work = Path(temporary)
        unpacked = work / 'unpacked'
        run('unsquashfs', '-no-progress', '-d', unpacked, args.image)
        module = json.loads((unpacked / 'usr/share/casaos/modules/apk.json').read_text())
        require(module['name'] == package['name'] and module['version'] == package['module_version'],
                'module identity does not match the recipe')
        require('ui' not in module, 'CLI module must omit dashboard metadata')
        base = unpacked / 'usr/share/zimaos-packages/apk/base'
        apk = official_apk(base, work / 'official-apk')
        header = run('readelf', '-h', base / 'sbin/apk')
        require('X86-64' in header.stdout, 'unexpected executable architecture')
        program_headers = run('readelf', '-l', base / 'sbin/apk')
        require('ld-musl-x86_64.so.1' in program_headers.stdout, 'unexpected apk loader')
        run(apk, '--version')
        # Official apk-tools 3 exits 1 after printing usage/help.
        help_result = run(apk, '--help', expect_success=False)
        require(help_result.returncode == 1 and 'mkpkg' in help_result.stdout,
                'built-in help is missing or returned an unexpected status')
        require((unpacked / 'usr/share/licenses/apk-tools/LICENSE').is_file(), 'upstream license missing')
        simple = work / 'simple'
        simple.mkdir()
        exercise_packages(apk, simple / 'root', simple)
    require(digest(args.image) == original, 'test modified the raw image')
    print('PASS: module metadata, official client, signatures, dependencies, simulation, '
          'upgrade, configuration preservation, corruption rejection and uninstall')


if __name__ == '__main__':
    main()
