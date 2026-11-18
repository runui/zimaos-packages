import gzip
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from common import digest, pack_tree, write_json
from manifest import extension_release, load_package, load_target
from repository import assemble, catalog
from test_build import definition


@unittest.skipUnless(shutil.which('mksquashfs') and shutil.which('unsquashfs'),
                     'squashfs-tools required')
class RepositoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='module repository ')
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.root = self.work / 'project'
        self.bundles = self.work / 'bundles'
        self.bundles.mkdir()
        self.output = self.work / 'site'
        self.base_url = 'https://example.invalid/packages'
        self.source = b'pinned upstream input'
        self.package = definition('demo', hashlib.sha512(self.source).hexdigest())
        self.directory = self.root / 'packages/demo'
        self.directory.mkdir(parents=True)
        (self.directory / 'build.sh').touch()
        write_json(self.directory / 'package.json', self.package)
        self.target = {'os_id': 'test-os', 'architecture': 'x86_64', 'sysext_architecture': 'x86-64'}
        write_json(self.root / 'targets/test-amd64.json', self.target)
        self.config = {'targets': ['test-amd64']}
        self.configure()

    def configure(self):
        write_json(self.root / 'repository.json', self.config)

    def bundle(self, fault=None, variant='original', target_name='test-amd64', source_data=None):
        target = load_target(self.root, target_name)
        package = load_package(self.root, 'demo', target['architecture'])
        destination = self.bundles / target_name
        destination.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.work) as temporary:
            work = Path(temporary)
            payload, files = work / 'payload', work / 'files'
            files.mkdir()
            info = {'package': 'demo', 'target': target_name,
                    'os_id': 'wrong' if fault == 'os_id' else target['os_id'],
                    'manifest': package,
                    **{key: package[key] for key in ('version', 'release', 'module_version', 'architecture', 'source')}}
            module = {'name': 'demo', 'version': package['module_version'], 'services': []}
            write_json(payload / 'usr/share/casaos/modules/demo.json', module)
            write_json(payload / 'usr/share/zimaos-packages/demo/build-info.json', info)
            release = payload / 'usr/lib/extension-release.d/extension-release.demo'
            release.parent.mkdir(parents=True)
            metadata = extension_release(target)
            release.write_text(metadata.replace('ARCHITECTURE=', 'WRONG_ARCHITECTURE=') if fault == 'compatibility' else metadata)
            (payload / 'usr/variant').write_text(variant)
            subprocess.run(['mksquashfs', str(payload), str(files / 'demo.raw'), '-noappend',
                            '-processors', '1', '-quiet', '-no-progress'], check=True, capture_output=True)
            write_json(files / 'demo.build-info.json', info)
            if fault == 'module':
                module['version'] = 'v9.0.0'
            write_json(files / 'demo.module.json', module)
            data = self.source if source_data is None else source_data
            (files / ('demo.source-' + package['source']['filename'])).write_bytes(b'corrupt' if fault == 'source' else data)
            sums = ''.join(f'{digest(path)}  {path.name}\n' for path in sorted(files.iterdir()))
            if fault == 'uncovered':
                sums = '\n'.join(sums.splitlines()[1:]) + '\n'
            (files / 'demo.SHA256SUMS').write_text(sums)
            if fault == 'checksum':
                (files / 'demo.raw').write_bytes(b'corrupted image')
            pack_tree(files, work / 'bundle.tar', 1700000000)
            bundle = destination / 'demo.bundle.tar.gz'
            bundle.write_bytes(gzip.compress((work / 'bundle.tar').read_bytes(), mtime=1700000000))
        return bundle

    def publish(self):
        assemble(self.root, self.bundles, self.output, self.base_url)

    def test_complete_store_has_compatible_indexes_and_verified_downloads(self):
        self.bundle()
        self.publish()
        store = json.loads((self.output / 'test-os/amd64/mod-v2.json').read_text())
        self.assertEqual(store[0]['version'], self.package['module_version'])
        records = json.loads((self.output / 'repository.json').read_text())['packages']
        self.assertEqual(len(records), 1)
        for record in records:
            self.assertEqual(record['catalog_url'], self.base_url + '/test-os/amd64/mod-v2.json')
            self.assertTrue(record['url'].startswith(self.base_url + '/test-os/amd64/packages/'))
            image = self.output / unquote(record['url'].removeprefix(self.base_url + '/'))
            self.assertTrue(image.is_file())
            self.assertEqual(digest(image), record['sha256'])
            self.assertEqual(image.stat().st_size, record['size_bytes'])
            for key in ('bundle_url', 'checksums_url'):
                self.assertTrue((self.output / unquote(record[key].removeprefix(self.base_url + '/'))).is_file())
        self.assertTrue((self.output / '.nojekyll').is_file())
        self.assertFalse(list(self.output.rglob('*.md')))

    def test_architectures_have_separate_sources_runners_and_store_entries(self):
        arm_source = b'pinned ARM64 input'
        self.package.pop('architecture')
        source = self.package.pop('source')
        self.package['sources'] = {'x86_64': source, 'aarch64': {
            'filename': 'arm64.tar', 'url': 'https://example.invalid/arm64.tar',
            'sha512': hashlib.sha512(arm_source).hexdigest()}}
        self.package['targets'].append('test-arm64')
        write_json(self.directory / 'package.json', self.package)
        write_json(self.root / 'targets/test-arm64.json', {
            'os_id': 'test-os', 'architecture': 'aarch64', 'sysext_architecture': 'arm64'})
        self.config['targets'].append('test-arm64')
        self.configure()
        _, matrix = catalog(self.root)
        self.assertEqual({item['target']: item['runner'] for item in matrix}, {
            'test-amd64': 'ubuntu-24.04', 'test-arm64': 'ubuntu-24.04-arm'})
        self.bundle()
        # A valid bundle checksum cannot make another architecture's source
        # archive acceptable for the selected target.
        self.bundle(target_name='test-arm64', source_data=self.source)
        with self.assertRaisesRegex(ValueError, 'source checksum mismatch'):
            self.publish()
        self.assertFalse(self.output.exists())
        self.bundle(target_name='test-arm64', source_data=arm_source)
        self.publish()
        x86 = json.loads((self.output / 'test-os/amd64/mod-v2.json').read_text())
        arm = json.loads((self.output / 'test-os/arm64/mod-v2.json').read_text())
        self.assertEqual(x86[0]['name'], arm[0]['name'])
        self.assertEqual(x86[0]['version'], arm[0]['version'])
        self.assertNotEqual(x86[0]['url'], arm[0]['url'])
        self.assertIn('/test-os/arm64/packages/', arm[0]['url'])
        records = json.loads((self.output / 'repository.json').read_text())['packages']
        self.assertEqual({record['architecture'] for record in records}, {'x86_64', 'aarch64'})

    def test_missing_duplicate_and_unexpected_bundles_do_not_publish(self):
        with self.assertRaisesRegex(ValueError, 'missing distribution'):
            self.publish()
        self.assertFalse(self.output.exists())
        first = self.bundle()
        duplicate = self.bundles / 'duplicate'
        duplicate.mkdir()
        shutil.copyfile(first, duplicate / first.name)
        with self.assertRaisesRegex(ValueError, 'unexpected or duplicate'):
            self.publish()
        self.assertFalse(self.output.exists())
        shutil.rmtree(duplicate)
        write_json(self.root / 'targets/test-other.json', self.target)
        self.bundle(target_name='test-other')
        with self.assertRaisesRegex(ValueError, 'unexpected or duplicate'):
            self.publish()
        self.assertFalse(self.output.exists())

    def test_corrupt_sources_checksums_and_embedded_metadata_do_not_publish(self):
        for fault, message in [('checksum', 'checksum mismatch'), ('source', 'source checksum'),
                               ('compatibility', 'image compatibility'), ('module', 'module identity'),
                               ('uncovered', 'cover every distribution file'), ('os_id', 'OS identity')]:
            with self.subTest(fault=fault):
                self.bundle(fault=fault)
                with self.assertRaisesRegex(ValueError, message):
                    self.publish()
                self.assertFalse(self.output.exists())

    def test_unsafe_or_duplicate_archive_members_are_rejected(self):
        for kind in ('traversal', 'link', 'duplicate'):
            with self.subTest(kind=kind):
                archive = self.bundles / 'unsafe.bundle.tar.gz'
                with tarfile.open(archive, 'w:gz') as stream:
                    entry = tarfile.TarInfo('../outside' if kind == 'traversal' else 'file')
                    if kind == 'link':
                        entry.type = tarfile.SYMTYPE
                        entry.linkname = '/etc/passwd'
                    else:
                        entry.size = 1
                    stream.addfile(entry, io.BytesIO(b'x') if entry.isfile() else None)
                    if kind == 'duplicate':
                        stream.addfile(entry, io.BytesIO(b'y'))
                with self.assertRaisesRegex(ValueError, 'invalid or oversized'):
                    self.publish()
                self.assertFalse(self.output.exists())
                self.assertFalse((self.work / 'outside').exists())

    def test_changed_bundle_gets_a_new_url_without_changing_module_identity(self):
        self.bundle()
        self.publish()
        first = json.loads((self.output / 'test-os/amd64/mod-v2.json').read_text())[0]
        shutil.rmtree(self.output)
        self.bundle(variant='changed runtime')
        self.publish()
        second = json.loads((self.output / 'test-os/amd64/mod-v2.json').read_text())[0]
        self.assertEqual(first['version'], second['version'])
        self.assertNotEqual(first['url'], second['url'])

    def test_invalid_release_configuration_and_download_origins_are_rejected(self):
        self.assertEqual(len(catalog(self.root)[1]), 1)
        for targets in (['test-amd64', 'test-amd64'], ['../outside'], []):
            with self.subTest(targets=targets):
                self.config['targets'] = targets
                self.configure()
                with self.assertRaises(ValueError):
                    catalog(self.root)
        for base in ('http://example.invalid', 'https://example.invalid/?query',
                     'https://user:password@example.invalid', 'relative'):
            with self.subTest(base=base), self.assertRaisesRegex(ValueError, 'base URL'):
                assemble(self.root, self.bundles, self.output, base)

    def test_targets_cannot_overwrite_the_same_public_directory(self):
        self.config['targets'].append('test-other')
        self.package['targets'].append('test-other')
        write_json(self.directory / 'package.json', self.package)
        write_json(self.root / 'targets/test-other.json', self.target)
        self.configure()
        with self.assertRaisesRegex(ValueError, 'distinct OS/architecture directories'):
            catalog(self.root)


if __name__ == '__main__':
    unittest.main()
