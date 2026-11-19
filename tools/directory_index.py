"""Render static directory listings and a GitHub Pages error page."""

from datetime import datetime, timezone
from html import escape
import os
from pathlib import Path
from string import Template
from urllib.parse import quote, urlsplit

from common import write_changed

TEMPLATES = Path(__file__).with_name('templates')


def write_indexes(root, base_url, epoch, *, download_urls=None):
    download_urls = download_urls or {}
    directory_template = Template((TEMPLATES / 'directory.html').read_text())
    error_template = Template((TEMPLATES / '404.html').read_text())
    base_path = urlsplit(base_url).path.rstrip('/') + '/'

    def write_page(path, template, modified, **values):
        write_changed(path, template.substitute(values))
        os.utime(path, (modified, modified))

    def visit(directory):
        entries = [path for path in directory.iterdir()
                   if not path.name.startswith('.') and path.name not in ('index.html', '404.html')]
        if any(path.is_symlink() for path in entries):
            raise ValueError('directory listings require regular files and directories')
        entries.sort(key=lambda path: (not path.is_dir(), path.name))
        for path in entries:
            if path.is_dir():
                visit(path)
        modified = max((path.stat().st_mtime for path in entries), default=epoch)
        relative = directory.relative_to(root).as_posix()
        public_path = base_path + (relative + '/' if relative != '.' else '')
        labels = [path.name + ('/' if path.is_dir() else '') for path in entries]
        width = max(50, max(map(len, labels), default=0)) + 2
        rows = ['<a href="../">../</a>'] if directory != root else []
        for path, label in zip(entries, labels):
            href = download_urls.get(path.relative_to(root))
            if href is None:
                href = quote(path.name, safe='') + ('/' if path.is_dir() else '')
            elif urlsplit(href).scheme != 'https' or not urlsplit(href).hostname:
                raise ValueError('external downloads require absolute HTTPS URLs')
            stat = path.stat()
            date = datetime.fromtimestamp(stat.st_mtime, timezone.utc).strftime('%Y-%m-%d %H:%M')
            size = '-' if path.is_dir() else str(stat.st_size)
            rows.append(f'<a href="{escape(href, quote=True)}">{escape(label)}</a>' + ' ' * (width - len(label))
                        + f'{date} {size:>12}')
        title = 'Index of ' + public_path
        body = (f'<h1>{escape(title)}</h1>\n<hr>\n'
                '<pre aria-label="Name, last modified in UTC, size in bytes">'
                + '\n'.join(rows) + '\n</pre>\n<hr>')
        write_page(directory / 'index.html', directory_template, modified, title=escape(title), body=body)
        os.utime(directory, (modified, modified))

    write_page(root / '404.html', error_template, epoch, root_url=escape(base_path, quote=True))
    visit(root)
