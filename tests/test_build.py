from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from common import extract_archive
from manifest import extension_release, load_package, load_target
from pipeline import fetch


def definition(name, checksum):
    return {'name': name, 'description': 'Temporary test package', 'version': '1.0.0',
            'release': 1, 'module_version': 'v1.0.0', 'architecture': 'x86_64',
            'targets': ['test-amd64'], 'source_date_epoch': 1700000000,
            'license': 'MIT', 'homepage': 'https://example.invalid/',
            'source': {'filename': 'payload.tar', 'url': 'https://example.invalid/payload.tar',
                       'sha512': checksum},
            'build': {'command': ['sh', 'build.sh'], 'inputs': ['build.sh']}}


class SourceIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.payload = b'verified upstream input'
        self.spec = {'filename': 'payload.tar', 'url': 'https://example.invalid/payload.tar',
                     'sha512': hashlib.sha512(self.payload).hexdigest()}
        self.output = self.root / 'downloaded.tar'
        self.cache = self.root / 'cache'
        self.cached = self.cache / (self.spec['sha512'] + '-payload.tar')

    def test_corrupt_cache_is_rejected_without_network(self):
        self.cached.parent.mkdir()
        self.cached.write_bytes(b'corrupt')
        with mock.patch('pipeline.urllib.request.urlopen') as download:
            with self.assertRaisesRegex(ValueError, 'cached source checksum mismatch'):
                fetch(self.spec, self.output, str(self.cache))
            download.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_corrupt_download_is_not_published(self):
        with mock.patch('pipeline.urllib.request.urlopen', return_value=io.BytesIO(b'corrupt')):
            with self.assertRaisesRegex(ValueError, 'source checksum mismatch'):
                fetch(self.spec, self.output, str(self.cache))
        self.assertFalse(self.output.exists())
        self.assertFalse(self.cached.exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_verified_cache_can_supply_another_build(self):
        with mock.patch('pipeline.urllib.request.urlopen', return_value=io.BytesIO(self.payload)) as download:
            fetch(self.spec, self.output, str(self.cache))
            self.output.unlink()
            fetch(self.spec, self.output, str(self.cache))
            self.assertEqual(download.call_count, 1)
        self.assertEqual(self.output.read_bytes(), self.payload)
        self.assertEqual(self.cached.read_bytes(), self.payload)


class ManifestTests(unittest.TestCase):
    def test_architecture_sources_are_selected_and_verified_independently(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / 'packages/demo'
            directory.mkdir(parents=True)
            (directory / 'build.sh').touch()
            package = definition('demo', '0' * 128)
            package.pop('architecture')
            source = package.pop('source')
            package['sources'] = {'x86_64': source, 'aarch64': {
                'filename': 'arm64.tar', 'url': 'https://example.invalid/arm64.tar', 'sha512': '1' * 128}}
            manifest = directory / 'package.json'
            manifest.write_text(json.dumps(package))
            for architecture, expected in package['sources'].items():
                resolved = load_package(root, 'demo', architecture)
                self.assertEqual(resolved['architecture'], architecture)
                self.assertEqual(resolved['source'], expected)
                self.assertNotIn('sources', resolved)
            with self.assertRaisesRegex(ValueError, 'does not support architecture'):
                load_package(root, 'demo', 'armv7')
            package['sources']['aarch64']['sha512'] = 'invalid'
            manifest.write_text(json.dumps(package))
            with self.assertRaisesRegex(ValueError, 'SHA-512'):
                load_package(root, 'demo', 'x86_64')
            package['source'] = source
            manifest.write_text(json.dumps(package))
            with self.assertRaisesRegex(ValueError, 'cannot be combined'):
                load_package(root, 'demo')

    def test_single_architecture_packages_reject_other_architectures(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / 'packages/demo'
            directory.mkdir(parents=True)
            (directory / 'build.sh').touch()
            (directory / 'package.json').write_text(json.dumps(definition('demo', '0' * 128)))
            self.assertEqual(load_package(root, 'demo', 'x86_64')['architecture'], 'x86_64')
            with self.assertRaisesRegex(ValueError, 'does not support architecture'):
                load_package(root, 'demo', 'aarch64')

    def test_default_images_have_no_host_version_constraint(self):
        for architecture in ('x86-64', 'arm64'):
            target = {'os_id': 'test', 'sysext_architecture': architecture}
            with self.subTest(architecture=architecture):
                metadata = extension_release(target)
                self.assertEqual(metadata, f'ID=_any\nARCHITECTURE={architecture}\nSYSEXT_SCOPE=system\n')
                self.assertNotIn('VERSION_ID=', metadata)

    def test_names_cannot_escape_repository(self):
        for loader in (load_package, load_target):
            with self.subTest(loader=loader.__name__), self.assertRaises(ValueError):
                loader(ROOT, '../../outside')

    def test_manifest_rejects_escaping_source_and_recipe_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / 'packages/demo'
            directory.mkdir(parents=True)
            for field in ('source', 'recipe'):
                package = definition('demo', '0' * 128)
                if field == 'source':
                    package['source']['filename'] = '../outside'
                else:
                    package['build']['inputs'] = ['../outside']
                (directory / 'package.json').write_text(json.dumps(package))
                with self.subTest(field=field), self.assertRaises(ValueError):
                    load_package(root, 'demo')


class ArchiveTests(unittest.TestCase):
    def extract(self, entries, **kwargs):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        archive = root / 'input.tar'
        with tarfile.open(archive, 'w') as stream:
            for entry, content in entries:
                stream.addfile(entry, io.BytesIO(content) if content is not None else None)
        extract_archive(archive, root / 'unpacked', **kwargs)
        return root / 'unpacked'

    def test_absolute_internal_link_is_relocated(self):
        entry = tarfile.TarInfo('etc/mtab')
        entry.type = tarfile.SYMTYPE
        entry.linkname = '/proc/mounts'
        root = self.extract([(entry, None)])
        self.assertEqual((root / 'etc/mtab').readlink(), Path('../proc/mounts'))

    def test_path_traversal_is_rejected(self):
        entry = tarfile.TarInfo('../outside')
        entry.size = 1
        with self.assertRaises(tarfile.FilterError):
            self.extract([(entry, b'x')])

    def test_escaping_link_is_rejected(self):
        entry = tarfile.TarInfo('escape')
        entry.type = tarfile.SYMTYPE
        entry.linkname = '../../outside'
        with self.assertRaises(tarfile.FilterError):
            self.extract([(entry, None)])

    def test_devices_and_excluded_contents_are_omitted(self):
        entry = tarfile.TarInfo('dev/runtime')
        entry.size = 1
        device = tarfile.TarInfo('zero')
        device.type = tarfile.CHRTYPE
        root = self.extract([(entry, b'x'), (device, None)], exclude_trees=('dev',))
        self.assertEqual(list(root.iterdir()), [])


@unittest.skipUnless(all(shutil.which(tool) for tool in ('meson', 'ninja', 'mksquashfs', 'unsquashfs')),
                     'Meson, Ninja, and squashfs-tools required')
class BuildGraphTests(unittest.TestCase):
    def invoke(self, *arguments, success=True):
        result = subprocess.run(list(map(str, arguments)), text=True, capture_output=True,
                                env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def count(self, name):
        return len((self.build / 'work' / name / 'runs').read_text())

    def test_architectures_build_concurrently_and_rebuild_independently(self):
        with tempfile.TemporaryDirectory(prefix='architecture graph ') as temporary:
            root = Path(temporary)
            source = root / 'project'
            source.mkdir()
            for name in ('meson.build', 'meson.options', 'LICENSE'):
                shutil.copyfile(ROOT / name, source / name)
            shutil.copytree(ROOT / 'tools', source / 'tools', ignore=shutil.ignore_patterns('__pycache__'))
            directory = source / 'packages/demo'
            directory.mkdir(parents=True)
            (directory / 'build.sh').write_text(
                'set -eu\nmkdir -p "$DESTDIR/usr/share/demo"\n'
                'printf x >> "$ZPKG_BUILD_DIR/runs"\n'
                'cp "$ZPKG_SOURCE_ARCHIVE" "$DESTDIR/usr/share/demo/source.tar"\n'
                'printf "%s" "$ZPKG_TARGET_ARCH" > "$DESTDIR/usr/share/demo/architecture"\n')
            package = definition('demo', '0' * 128)
            package.pop('source')
            package.pop('architecture')
            package.update(targets=['test-amd64', 'test-arm64'], sources={})
            (source / 'targets').mkdir()
            cache = root / 'cache'
            cache.mkdir()
            variants = [('test-amd64', 'x86_64', 'x86-64'), ('test-arm64', 'aarch64', 'arm64')]
            for target, architecture, sysext in variants:
                (source / 'targets' / (target + '.json')).write_text(json.dumps({
                    'os_id': 'test-os', 'architecture': architecture, 'sysext_architecture': sysext}))
                archive = root / (architecture + '.tar')
                with tarfile.open(archive, 'w') as stream:
                    data = architecture.encode()
                    entry = tarfile.TarInfo('architecture')
                    entry.size = len(data)
                    stream.addfile(entry, io.BytesIO(data))
                checksum = hashlib.sha512(archive.read_bytes()).hexdigest()
                package['sources'][architecture] = {'filename': archive.name,
                    'url': 'https://example.invalid/' + archive.name, 'sha512': checksum}
                shutil.copyfile(archive, cache / (checksum + '-' + archive.name))
            (directory / 'package.json').write_text(json.dumps(package))
            for target, architecture, sysext in variants:
                build = root / target
                self.invoke('meson', 'setup', build, source, '-Dpackages=demo', '-Dtarget=' + target,
                            '-Dsource_cache=' + str(cache))
            with ThreadPoolExecutor(max_workers=2) as executor:
                builds = [executor.submit(self.invoke, 'meson', 'compile', '-C', root / target, 'bundles')
                          for target, _, _ in variants]
                for build in builds:
                    build.result()
            for target, architecture, sysext in variants:
                build = root / target
                info = json.loads((build / 'demo.build-info.json').read_text())
                self.assertEqual(info['architecture'], architecture)
                self.assertEqual(info['os_id'], 'test-os')
                self.assertEqual(info['source'], package['sources'][architecture])
                self.assertEqual(self.invoke('unsquashfs', '-cat', build / 'demo.raw',
                                            'usr/share/demo/architecture'), architecture)
                metadata = self.invoke('unsquashfs', '-cat', build / 'demo.raw',
                                       'usr/lib/extension-release.d/extension-release.demo')
                self.assertIn('ARCHITECTURE=' + sysext + '\n', metadata)
                self.assertIn('ID=_any\n', metadata)
                self.assertNotIn('VERSION_ID=', metadata)
            amd64 = root / 'test-amd64'
            arm64 = root / 'test-arm64'
            amd64_time = (amd64 / 'demo.raw').stat().st_mtime_ns
            amd64_image = (amd64 / 'demo.raw').read_bytes()
            arm64_image = (arm64 / 'demo.raw').read_bytes()
            # Only the ARM source changes; both builds share the same checkout.
            arm_source = package['sources']['aarch64']
            changed_source = b'updated ARM source'
            arm_source['sha512'] = hashlib.sha512(changed_source).hexdigest()
            (cache / (arm_source['sha512'] + '-' + arm_source['filename'])).write_bytes(changed_source)
            (directory / 'package.json').write_text(json.dumps(package))
            with ThreadPoolExecutor(max_workers=2) as executor:
                builds = [executor.submit(self.invoke, 'meson', 'compile', '-C', build, 'bundles')
                          for build in (amd64, arm64)]
                for build in builds:
                    build.result()
            self.assertEqual((amd64 / 'demo.raw').stat().st_mtime_ns, amd64_time)
            self.assertEqual((amd64 / 'demo.raw').read_bytes(), amd64_image)
            self.assertNotEqual((arm64 / 'demo.raw').read_bytes(), arm64_image)
            self.assertEqual((amd64 / 'work/demo/runs').read_text(), 'x')
            self.assertEqual((arm64 / 'work/demo/runs').read_text(), 'xx')
            # A clean amd64 build must also produce the same bytes after an
            # unrelated architecture's source pin changes in the manifest.
            clean_amd64 = root / 'clean-amd64'
            self.invoke('meson', 'setup', clean_amd64, source, '-Dpackages=demo', '-Dtarget=test-amd64',
                        '-Dsource_cache=' + str(cache))
            self.invoke('meson', 'compile', '-C', clean_amd64)
            self.assertEqual((clean_amd64 / 'demo.raw').read_bytes(), amd64_image)
            (arm64 / ('demo.source-' + arm_source['filename'])).write_bytes(b'corrupt ARM download')
            failure = self.invoke('meson', 'compile', '-C', arm64, success=False)
            self.assertIn('source checksum mismatch', failure)
            self.assertIn('no work to do', self.invoke('meson', 'compile', '-C', amd64))
            self.assertEqual((amd64 / 'demo.raw').read_bytes(), amd64_image)

    def test_languages_incremental_dependencies_and_failure_recovery(self):
        with tempfile.TemporaryDirectory(prefix='extension graph ') as temporary:
            root = Path(temporary)
            source = root / 'project'
            source.mkdir()
            self.build = root / 'build'
            for name in ('meson.build', 'meson.options', 'LICENSE'):
                shutil.copyfile(ROOT / name, source / name)
            shutil.copytree(ROOT / 'tools', source / 'tools', ignore=shutil.ignore_patterns('__pycache__'))
            (source / 'targets').mkdir()
            (source / 'targets/test-amd64.json').write_text(json.dumps({
                'os_id': 'test-os', 'architecture': 'x86_64', 'sysext_architecture': 'x86-64'}))
            archive = root / 'payload.tar'
            with tarfile.open(archive, 'w') as stream:
                entry = tarfile.TarInfo('message.txt')
                entry.size = 8
                stream.addfile(entry, io.BytesIO(b'payload\n'))
            checksum = hashlib.sha512(archive.read_bytes()).hexdigest()
            cache = root / 'cache'
            cache.mkdir()
            shutil.copyfile(archive, cache / (checksum + '-payload.tar'))
            for name in ('alpha', 'beta'):
                directory = source / 'packages' / name
                directory.mkdir(parents=True)
                package = definition(name, checksum)
                if name == 'alpha':
                    (directory / 'build.sh').write_text(
                        'set -eu\n'
                        'printf x >> "$ZPKG_BUILD_DIR/runs"\n'
                        'mkdir -p "$DESTDIR/usr/share/alpha"\n'
                        'cp "$ZPKG_SOURCE_ARCHIVE" "$DESTDIR/usr/share/alpha/source.tar"\n'
                        'printf report > "$ZPKG_ARTIFACTS_DIR/report.txt"\n')
                    package['build']['artifacts'] = ['report.txt']
                    config = directory / 'files/usr/share/alpha/config.txt'
                    config.parent.mkdir(parents=True)
                    config.write_text('initial\n')
                else:
                    (directory / 'recipe.py').write_text(
                        'import os, pathlib, shutil\n'
                        'work = pathlib.Path(os.environ["ZPKG_BUILD_DIR"])\n'
                        'with (work / "runs").open("a") as stream: stream.write("x")\n'
                        'dest = pathlib.Path(os.environ["DESTDIR"]) / "usr/share/beta"\n'
                        'dest.mkdir(parents=True)\n'
                        'shutil.copyfile(os.environ["ZPKG_SOURCE_ARCHIVE"], dest / "source.tar")\n')
                    package['build'] = {'command': [sys.executable, 'recipe.py'], 'inputs': ['recipe.py']}
                (directory / 'package.json').write_text(json.dumps(package))
            self.invoke('meson', 'setup', self.build, source, '-Dpackages=all', '-Dtarget=test-amd64',
                        '-Dsource_cache=' + str(cache))
            self.invoke('meson', 'compile', '-C', self.build, '-j', '2')
            self.assertEqual((self.count('alpha'), self.count('beta')), (1, 1))
            for name in ('alpha', 'beta'):
                for line in (self.build / (name + '.SHA256SUMS')).read_text().splitlines():
                    value, filename = line.split('  ')
                    self.assertEqual(hashlib.sha256((self.build / filename).read_bytes()).hexdigest(), value)
            self.assertEqual((self.build / 'alpha.report.txt').read_text(), 'report')
            self.invoke('meson', 'compile', '-C', self.build, 'bundles')
            with tarfile.open(self.build / 'alpha.bundle.tar.gz') as bundle:
                self.assertIn('alpha.raw', bundle.getnames())
                self.assertIn('alpha.SHA256SUMS', bundle.getnames())
                self.assertNotIn('alpha.stage.tar', bundle.getnames())
            beta_time = (self.build / 'beta.raw').stat().st_mtime_ns
            source_time = (self.build / 'alpha.source-payload.tar').stat().st_mtime_ns
            initial_raw = (self.build / 'alpha.raw').read_bytes()
            self.assertIn('no work to do', self.invoke('meson', 'compile', '-C', self.build))
            self.assertEqual((self.count('alpha'), self.count('beta')), (1, 1))
            config.write_text('changed\n')
            self.invoke('meson', 'compile', '-C', self.build)
            self.assertEqual((self.count('alpha'), self.count('beta')), (1, 1))
            self.assertNotEqual((self.build / 'alpha.raw').read_bytes(), initial_raw)
            self.assertEqual((self.build / 'beta.raw').stat().st_mtime_ns, beta_time)
            self.assertEqual((self.build / 'alpha.source-payload.tar').stat().st_mtime_ns, source_time)
            recipe = source / 'packages/alpha/build.sh'
            recipe.write_text(recipe.read_text() + '# recipe revision\n')
            self.invoke('meson', 'compile', '-C', self.build)
            self.assertEqual((self.count('alpha'), self.count('beta')), (2, 1))
            added = config.with_name('added.txt')
            added.write_text('new payload\n')
            self.invoke('meson', 'setup', '--reconfigure', self.build, source)
            self.invoke('meson', 'compile', '-C', self.build)
            self.assertEqual((self.count('alpha'), self.count('beta')), (2, 1))
            self.assertEqual(self.invoke('unsquashfs', '-cat', self.build / 'alpha.raw',
                                         'usr/share/alpha/added.txt'), 'new payload\n')
            good = (self.build / 'alpha.raw').read_bytes()
            (self.build / 'alpha.raw').unlink()
            self.invoke('meson', 'compile', '-C', self.build)
            self.assertEqual((self.build / 'alpha.raw').read_bytes(), good)
            self.assertEqual(self.count('alpha'), 2)
            downloaded = self.build / 'alpha.source-payload.tar'
            downloaded.write_bytes(b'corrupt archive')
            failure = self.invoke('meson', 'compile', '-C', self.build, success=False)
            self.assertIn('source checksum mismatch before package execution', failure)
            self.assertEqual(self.count('alpha'), 2)
            self.assertEqual((self.build / 'alpha.raw').read_bytes(), good)
            downloaded.write_bytes(archive.read_bytes())
            self.invoke('meson', 'compile', '-C', self.build)
            self.assertEqual(self.count('alpha'), 3)
            recipe.write_text('exit 17\n')
            self.invoke('meson', 'compile', '-C', self.build, success=False)
            self.assertEqual((self.build / 'alpha.raw').read_bytes(), good)


if __name__ == '__main__':
    unittest.main()
