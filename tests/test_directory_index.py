from html.parser import HTMLParser
import os
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from directory_index import write_indexes


class Links(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.hrefs = []
        self.feed(content)

    def handle_starttag(self, tag, attributes):
        if tag == 'a':
            self.hrefs.append(dict(attributes)['href'])


class DirectoryIndexTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='directory index ')
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.site = self.work / 'site'
        self.site.mkdir()
        self.epoch = 1700000000

    def file(self, relative, content=b'package data'):
        path = self.site / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        os.utime(path, (self.epoch, self.epoch))
        return path

    def test_all_directories_have_navigable_indexes_and_escaped_filenames(self):
        self.file('repository.json', b'{}\n')
        self.file('zimaos/amd64/mod-v2.json', b'[]\n')
        name = '<package> & "archive" #1.raw'
        self.file('zimaos/amd64/packages/' + name, b'raw image')
        self.file('.nojekyll', b'')
        (self.site / 'empty').mkdir()
        write_indexes(self.site, 'https://example.invalid/project', self.epoch)
        for directory in [self.site, *[path for path in self.site.rglob('*') if path.is_dir()]]:
            self.assertTrue((directory / 'index.html').is_file())
        root = (self.site / 'index.html').read_text()
        self.assertEqual(Links(root).hrefs, ['empty/', 'zimaos/', 'repository.json'])
        self.assertIn('Index of /project/', root)
        nested = (self.site / 'zimaos/amd64/index.html').read_text()
        self.assertEqual(Links(nested).hrefs, ['../', 'packages/', 'mod-v2.json'])
        self.assertIn('Index of /project/zimaos/amd64/', nested)
        files = (self.site / 'zimaos/amd64/packages/index.html').read_text()
        self.assertEqual(Links(files).hrefs, ['../', quote(name, safe='')])
        self.assertIn('&lt;package&gt; &amp; &quot;archive&quot; #1.raw', files)
        self.assertNotIn('<package>', files)
        self.assertIn('2023-11-14 22:13', files)
        self.assertIn('           9', files)
        self.assertEqual(Links((self.site / 'empty/index.html').read_text()).hrefs, ['../'])

    def test_error_page_links_to_the_site_root_for_projects_and_custom_domains(self):
        for base_url, root_link in [('https://example.invalid/project', '/project/'),
                                    ('https://downloads.example.invalid', '/')]:
            with self.subTest(base_url=base_url):
                write_indexes(self.site, base_url, self.epoch)
                error = (self.site / '404.html').read_text()
                self.assertIn('<title>404 Not Found</title>', error)
                self.assertIn('<h1>404 Not Found</h1>', error)
                self.assertEqual(Links(error).hrefs, [root_link])

    def test_regeneration_is_stable_and_preserves_downloads(self):
        image = self.file('zimaos/arm64/package.raw')
        write_indexes(self.site, 'https://example.invalid/project', self.epoch)
        snapshot = {path.relative_to(self.site): path.read_bytes() for path in self.site.rglob('*.html')}
        os.utime(image.parent, (self.epoch + 500, self.epoch + 500))
        write_indexes(self.site, 'https://example.invalid/project', self.epoch)
        self.assertEqual(snapshot, {path.relative_to(self.site): path.read_bytes()
                                   for path in self.site.rglob('*.html')})
        self.assertEqual(image.read_bytes(), b'package data')
        self.assertEqual(image.stat().st_mtime, self.epoch)

    def test_links_cannot_expose_files_outside_the_publication_tree(self):
        outside = self.work / 'private'
        outside.write_text('private data')
        (self.site / 'link').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'regular files and directories'):
            write_indexes(self.site, 'https://example.invalid/project', self.epoch)
        self.assertEqual(outside.read_text(), 'private data')


if __name__ == '__main__':
    unittest.main()
