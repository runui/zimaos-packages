from contextlib import redirect_stdout
import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import publication
from publication import prepare_pages, publish


@unittest.skipUnless(shutil.which('git') and shutil.which('git-lfs'), 'Git and Git LFS required')
class PublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='LFS publication ')
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.environment = mock.patch.dict(os.environ, {
            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': str(self.work / 'global.gitconfig'),
            'GIT_TERMINAL_PROMPT': '0', 'GIT_LFS_SKIP_SMUDGE': '1', 'GITHUB_TOKEN': ''})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.remote = self.work / 'remote.git'
        self.git('init', '--bare', '--template=', str(self.remote))
        self.site = self.work / 'site'
        self.image = self.site / 'packages/demo.raw'
        self.image.parent.mkdir(parents=True)
        self.content = os.urandom(2048)
        self.image.write_bytes(self.content)
        (self.site / '.gitattributes').write_text('/packages/demo.raw filter=lfs diff=lfs merge=lfs -text\n')
        (self.site / '.nojekyll').touch()
        (self.site / 'repository.json').write_text('{}\n')
        (self.site / 'index.html').write_text(
            '<a href="https://media.githubusercontent.com/media/example/packages/gh-pages/packages/demo.raw">demo.raw</a>\n')
        (self.site / 'small.txt').write_text('served by Pages\n')

    def git(self, *arguments, **kwargs):
        return subprocess.run(['git', *arguments], capture_output=True, check=True, **kwargs).stdout

    def head(self):
        return self.git('--git-dir=' + str(self.remote), 'rev-parse', 'gh-pages').strip()

    def clone(self, name='checkout'):
        checkout = self.work / name
        self.git('clone', '--branch', 'gh-pages', str(self.remote), str(checkout))
        return checkout

    def hydrate(self, checkout):
        self.git('-C', str(checkout), 'lfs', 'install', '--local')
        self.git('-C', str(checkout), 'lfs', 'pull', env={**os.environ, 'GIT_LFS_SKIP_SMUDGE': '0'})

    def test_first_publication_stores_pointers_and_uploads_complete_objects(self):
        self.assertTrue(publish(self.site, str(self.remote)))
        pointer = self.git('--git-dir=' + str(self.remote), 'show', 'gh-pages:packages/demo.raw')
        checksum = hashlib.sha256(self.content).hexdigest()
        self.assertTrue(pointer.startswith(b'version https://git-lfs.github.com/spec/v1\n'))
        self.assertIn(('oid sha256:' + checksum).encode(), pointer)
        self.assertIn(('size ' + str(len(self.content))).encode(), pointer)
        objects = [path for path in self.remote.rglob(checksum) if path.is_file()]
        self.assertEqual(len(objects), 1)
        self.assertEqual(objects[0].read_bytes(), self.content)
        checkout = self.clone()
        self.assertEqual((checkout / 'packages/demo.raw').read_bytes(), pointer)
        self.hydrate(checkout)
        self.assertEqual((checkout / 'packages/demo.raw').read_bytes(), self.content)
        self.assertEqual(self.image.read_bytes(), self.content)
        self.assertFalse(list(self.work.glob('publication-*')))

    def test_repeated_publication_is_a_noop_and_updates_preserve_history(self):
        publish(self.site, str(self.remote))
        previous = self.head()
        self.assertFalse(publish(self.site, str(self.remote)))
        self.assertEqual(self.head(), previous)
        self.image.write_bytes(b'new complete image')
        (self.site / 'small.txt').unlink()
        self.assertTrue(publish(self.site, str(self.remote)))
        self.assertNotEqual(self.head(), previous)
        parent = self.git('--git-dir=' + str(self.remote), 'rev-parse', 'gh-pages^').strip()
        self.assertEqual(parent, previous)
        checkout = self.clone()
        self.assertFalse((checkout / 'small.txt').exists())
        self.hydrate(checkout)
        self.assertEqual((checkout / 'packages/demo.raw').read_bytes(), b'new complete image')

    def test_failed_lfs_upload_does_not_advance_the_remote_branch(self):
        publish(self.site, str(self.remote))
        previous = self.head()
        self.image.write_bytes(b'unpublished image')
        original_run = publication.run
        calls = []

        def fail_upload(*arguments, **kwargs):
            calls.append(arguments)
            if arguments[3:5] == ('lfs', 'push'):
                raise subprocess.CalledProcessError(1, list(map(str, arguments)))
            return original_run(*arguments, **kwargs)

        with mock.patch('publication.run', side_effect=fail_upload), self.assertRaises(subprocess.CalledProcessError):
            publish(self.site, str(self.remote))
        self.assertEqual(self.head(), previous)
        self.assertFalse(any(arguments[3] == 'push' for arguments in calls))
        self.assertFalse(list(self.work.glob('publication-*')))

    def test_untracked_large_content_is_rejected_before_upload_or_branch_push(self):
        (self.site / '.gitattributes').unlink()
        with mock.patch('publication.MAX_FILE_BYTES', 1024), self.assertRaisesRegex(ValueError, 'not stored in Git LFS'):
            publish(self.site, str(self.remote))
        self.assertEqual(self.git('--git-dir=' + str(self.remote), 'for-each-ref'), b'')

    def test_concurrent_publication_is_rejected_without_forcing_the_branch(self):
        publish(self.site, str(self.remote))
        concurrent = self.clone('concurrent')
        self.git('-C', str(concurrent), 'config', 'user.name', 'Temporary publisher')
        self.git('-C', str(concurrent), 'config', 'user.email', 'publisher@example.invalid')
        self.image.write_bytes(b'pending publication')
        original_run = publication.run
        advanced = []

        def advance_before_push(*arguments, **kwargs):
            if arguments[3] == 'push':
                (concurrent / 'concurrent.txt').write_text('independent update\n')
                self.git('-C', str(concurrent), 'add', 'concurrent.txt')
                self.git('-C', str(concurrent), 'commit', '-m', 'Concurrent publication')
                self.git('-C', str(concurrent), 'push', 'origin', 'HEAD:gh-pages')
                advanced.append(self.head())
            return original_run(*arguments, **kwargs)

        with mock.patch('publication.run', side_effect=advance_before_push), self.assertRaises(subprocess.CalledProcessError):
            publish(self.site, str(self.remote))
        self.assertEqual(advanced, [self.head()])
        self.assertEqual(self.git('--git-dir=' + str(self.remote), 'show', 'gh-pages:concurrent.txt'),
                         b'independent update\n')

    def test_pages_excludes_lfs_pointers_and_keeps_indexes_and_small_files(self):
        publish(self.site, str(self.remote))
        checkout = self.clone()
        prepare_pages(checkout)
        self.assertFalse((checkout / 'packages/demo.raw').exists())
        self.assertFalse((checkout / '.gitattributes').exists())
        for name in ('index.html', 'repository.json', 'small.txt', '.nojekyll'):
            self.assertEqual((checkout / name).read_bytes(), (self.site / name).read_bytes())
        prepare_pages(checkout)
        self.assertEqual(self.image.read_bytes(), self.content)

    def test_pages_keeps_small_files_when_lfs_is_not_used(self):
        (self.site / '.gitattributes').unlink()
        publish(self.site, str(self.remote))
        checkout = self.clone()
        prepare_pages(checkout)
        self.assertEqual((checkout / 'packages/demo.raw').read_bytes(), self.content)

    def test_pages_rejects_remaining_pointers_and_links_outside_its_checkout(self):
        publish(self.site, str(self.remote))
        checkout = self.clone()
        pointer = (checkout / 'packages/demo.raw').read_bytes()
        (checkout / 'untracked.raw').write_bytes(pointer)
        with self.assertRaisesRegex(ValueError, 'pointer remains'):
            prepare_pages(checkout)
        linked = self.clone('linked-checkout')
        (linked / 'packages/demo.raw').unlink()
        (linked / 'packages/demo.raw').symlink_to(self.image)
        with self.assertRaisesRegex(ValueError, 'escapes'):
            prepare_pages(linked)
        self.assertEqual(self.image.read_bytes(), self.content)

    def test_credentials_use_an_environment_helper_without_recording_the_token(self):
        original_run = publication.run
        checked = []

        def inspect_helper(*arguments, **kwargs):
            result = original_run(*arguments, **kwargs)
            if arguments[3:7] == ('config', '--local', '--add', 'credential.helper'):
                checkout = Path(arguments[2])
                credentials = self.git('-C', str(checkout), 'credential', 'fill',
                                       input=b'protocol=https\nhost=example.invalid\n\n')
                self.assertIn(b'password=temporary-test-token\n', credentials)
                self.assertNotIn('temporary-test-token', (checkout / '.git/config').read_text())
                checked.append(True)
            return result

        output = io.StringIO()
        with mock.patch.dict(os.environ, {'GITHUB_TOKEN': 'temporary-test-token'}), \
                mock.patch('publication.run', side_effect=inspect_helper), redirect_stdout(output):
            publish(self.site, str(self.remote))
        self.assertEqual(checked, [True])
        self.assertNotIn('temporary-test-token', output.getvalue())


if __name__ == '__main__':
    unittest.main()
