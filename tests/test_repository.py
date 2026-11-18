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
from manifest import extension_release
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
        self.config = {'default': {'target': 'test-amd64', 'os_version': '1.0'},
                       'releases': [{'target': 'test-amd64', 'os_versions': ['1.0', '2.0']}]}
        self.configure()

    def configure(self):
        write_json(self.root / 'repository.json', self.config)

    def bundle(self, os_version, fault=None, variant='original'):
        destination = self.bundles / os_version
        destination.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.work) as temporary:
            work = Path(temporary)
            payload, files = work / 'payload', work / 'files'
            files.mkdir()
            info = {'package': 'demo', 'target': 'test-amd64', 'os_version': os_version,
                    **{key: self.package[key] for key in ('version', 'release', 'module_version', 'architecture', 'source')}}
            module = {'name': 'demo', 'version': self.package['module_version'], 'services': []}
            write_json(payload / 'usr/share/casaos/modules/demo.json', module)
            write_json(payload / 'usr/share/zimaos-packages/demo/build-info.json', info)
            release = payload / 'usr/lib/extension-release.d/extension-release.demo'
            release.parent.mkdir(parents=True)
            release.write_text(extension_release(self.target, 'wrong' if fault == 'compatibility' else os_version))
            (payload / 'usr/variant').write_text(variant)
            subprocess.run(['mksquashfs', str(payload), str(files / 'demo.raw'), '-noappend',
                            '-processors', '1', '-quiet', '-no-progress'], check=True, capture_output=True)
            write_json(files / 'demo.build-info.json', info)
            if fault == 'module':
                module['version'] = 'v9.0.0'
            write_json(files / 'demo.module.json', module)
            (files / 'demo.source-payload.tar').write_bytes(b'corrupt' if fault == 'source' else self.source)
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
        self.bundle('1.0')
        self.bundle('2.0')
        self.publish()
        root_store = json.loads((self.output / 'mod-v2.json').read_text())
        self.assertEqual(root_store, json.loads((self.output / 'mod.json').read_text()))
        self.assertEqual(root_store, json.loads((self.output / 'test-amd64/1.0/mod-v2.json').read_text()))
        self.assertNotEqual(root_store, json.loads((self.output / 'test-amd64/2.0/mod-v2.json').read_text()))
        self.assertEqual(root_store[0]['version'], self.package['module_version'])
        records = json.loads((self.output / 'repository.json').read_text())['packages']
        self.assertEqual({item['os_version'] for item in records}, {'1.0', '2.0'})
        for record in records:
            image = self.output / unquote(record['url'].removeprefix(self.base_url + '/'))
            self.assertTrue(image.is_file())
            self.assertEqual(digest(image), record['sha256'])
            self.assertEqual(image.stat().st_size, record['size_bytes'])
            for key in ('bundle_url', 'checksums_url'):
                self.assertTrue((self.output / unquote(record[key].removeprefix(self.base_url + '/'))).is_file())
        self.assertTrue((self.output / '.nojekyll').is_file())
        self.assertFalse(list(self.output.rglob('*.md')))

    def test_missing_duplicate_and_unexpected_bundles_do_not_publish(self):
        with self.assertRaisesRegex(ValueError, 'missing distribution'):
            self.publish()
        self.assertFalse(self.output.exists())
        first = self.bundle('1.0')
        self.bundle('2.0')
        duplicate = self.bundles / 'duplicate'
        duplicate.mkdir()
        shutil.copyfile(first, duplicate / first.name)
        with self.assertRaisesRegex(ValueError, 'unexpected or duplicate'):
            self.publish()
        self.assertFalse(self.output.exists())
        shutil.rmtree(duplicate)
        self.bundle('3.0')
        with self.assertRaisesRegex(ValueError, 'unexpected or duplicate'):
            self.publish()
        self.assertFalse(self.output.exists())

    def test_corrupt_sources_checksums_and_embedded_metadata_do_not_publish(self):
        self.bundle('2.0')
        for fault, message in [('checksum', 'checksum mismatch'), ('source', 'source checksum'),
                               ('compatibility', 'image compatibility'), ('module', 'module identity'),
                               ('uncovered', 'cover every distribution file')]:
            with self.subTest(fault=fault):
                self.bundle('1.0', fault=fault)
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
        self.bundle('1.0')
        self.bundle('2.0')
        self.publish()
        first = json.loads((self.output / 'mod-v2.json').read_text())[0]
        shutil.rmtree(self.output)
        self.bundle('1.0', variant='changed runtime')
        self.publish()
        second = json.loads((self.output / 'mod-v2.json').read_text())[0]
        self.assertEqual(first['version'], second['version'])
        self.assertNotEqual(first['url'], second['url'])

    def test_invalid_release_configuration_and_download_origins_are_rejected(self):
        self.assertEqual(len(catalog(self.root)[1]), 2)
        for versions in (['1.0', '1.0'], ['0.0.0'], ['../outside'], []):
            with self.subTest(versions=versions):
                self.config['releases'][0]['os_versions'] = versions
                self.configure()
                with self.assertRaises(ValueError):
                    catalog(self.root)
        for base in ('http://example.invalid', 'https://example.invalid/?query',
                     'https://user:password@example.invalid', 'relative'):
            with self.subTest(base=base), self.assertRaisesRegex(ValueError, 'base URL'):
                assemble(self.root, self.bundles, self.output, base)


if __name__ == '__main__':
    unittest.main()
