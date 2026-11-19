#!/usr/bin/env python3
"""Publish a module repository with Git LFS and prepare its Pages payload."""

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

from common import run
from repository import MAX_FILE_BYTES


def lfs_paths(checkout):
    result = subprocess.check_output(['git', '-C', str(checkout), 'lfs', 'ls-files', '--json'])
    paths = [Path(entry['name']) for entry in (json.loads(result).get('files') or [])]
    if any(path.is_absolute() or '..' in path.parts for path in paths):
        raise ValueError('invalid LFS publication path')
    return paths


def publish(site, remote, branch='gh-pages', message='Publish module repository'):
    site = site.resolve()
    if not site.is_dir() or (site / '.git').exists():
        raise ValueError('publication input must be a generated site without a .git directory')
    if any(path.is_symlink() for path in site.rglob('*')):
        raise ValueError('publication input must not contain symbolic links')
    parsed = urlsplit(remote)
    if parsed.username or parsed.password:
        raise ValueError('publication remote must not contain credentials')
    environment = {**os.environ, 'GIT_LFS_SKIP_SMUDGE': '1', 'GIT_TERMINAL_PROMPT': '0'}
    with tempfile.TemporaryDirectory(prefix='publication-', dir=site.parent) as temporary:
        work = Path(temporary)
        checkout = work / 'repository'
        run('git', 'init', '--template=', '--initial-branch=' + branch, checkout, env=environment)

        def git(*arguments, **kwargs):
            return run('git', '-C', checkout, *arguments, env=environment, **kwargs)

        git('check-ref-format', '--branch', branch)
        git('config', '--local', 'user.name', 'github-actions[bot]')
        git('config', '--local', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
        git('config', '--local', 'commit.gpgsign', 'false')
        git('config', '--local', 'core.hooksPath', str(checkout / '.git/hooks'))
        git('config', '--local', 'lfs.storage', str(checkout / '.git/lfs'))
        git('config', '--local', 'credential.helper', '')
        if environment.get('GITHUB_TOKEN'):
            helper = work / 'credentials'
            helper.write_text('#!/bin/sh\n'
                              'if [ "$1" = get ]; then\n'
                              '  printf "%s\\n" "username=x-access-token"\n'
                              '  printf "password=%s\\n" "$GITHUB_TOKEN"\n'
                              'fi\n')
            helper.chmod(0o700)
            # Git parses credential helpers as shell commands; quote paths that
            # may contain spaces without expanding the token into configuration.
            git('config', '--local', '--add', 'credential.helper', '!' + shlex.quote(str(helper)))
        git('remote', 'add', 'origin', remote)
        git('lfs', 'install', '--local', '--skip-smudge')
        existing = subprocess.run(['git', '-C', str(checkout), 'ls-remote', '--exit-code',
                                   '--heads', 'origin', 'refs/heads/' + branch],
                                  env=environment, capture_output=True, check=False)
        if existing.returncode == 0:
            git('fetch', '--depth=1', 'origin', 'refs/heads/' + branch)
            git('checkout', '-B', branch, 'FETCH_HEAD')
        elif existing.returncode != 2:
            raise subprocess.CalledProcessError(existing.returncode, existing.args,
                                                output=existing.stdout, stderr=existing.stderr)
        for path in checkout.iterdir():
            if path.name != '.git':
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink()
        shutil.copytree(site, checkout, dirs_exist_ok=True)
        git('add', '--all')
        tracked = set(lfs_paths(checkout))
        for path in site.rglob('*'):
            if path.is_file() and path.stat().st_size >= MAX_FILE_BYTES and path.relative_to(site) not in tracked:
                raise ValueError(f'large publication file is not stored in Git LFS: {path.relative_to(site)}')
        changed = subprocess.run(['git', '-C', str(checkout), 'diff', '--cached', '--quiet'],
                                 env=environment, check=False).returncode
        if changed not in (0, 1):
            raise ValueError('cannot inspect publication changes')
        if changed:
            git('commit', '--message', message)
        # Upload first so the branch never advertises unavailable LFS objects.
        # A repeated publication can repair uploads without creating a commit.
        git('lfs', 'push', 'origin', branch)
        if changed:
            git('push', 'origin', 'HEAD:refs/heads/' + branch)
        return bool(changed)


def prepare_pages(checkout):
    checkout = checkout.resolve()
    top = subprocess.check_output(['git', '-C', str(checkout), 'rev-parse', '--show-toplevel'], text=True).strip()
    if Path(top).resolve() != checkout:
        raise ValueError('Pages preparation requires the root of a disposable checkout')
    for relative in lfs_paths(checkout):
        path = checkout / relative
        if path.is_symlink() or not path.resolve().is_relative_to(checkout):
            raise ValueError('LFS publication path escapes the Pages checkout')
        path.unlink(missing_ok=True)
    (checkout / '.gitattributes').unlink(missing_ok=True)
    for directory, children, files in os.walk(checkout):
        children[:] = [name for name in children if name != '.git']
        for name in files:
            path = Path(directory) / name
            if path.is_symlink():
                raise ValueError('Pages payload must not contain symbolic links')
            with path.open('rb') as stream:
                if stream.read(43).startswith(b'version https://git-lfs.github.com/spec/v1\n'):
                    raise ValueError(f'LFS pointer remains in Pages payload: {path.relative_to(checkout)}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    upload = commands.add_parser('publish')
    upload.add_argument('--site', type=Path, required=True)
    upload.add_argument('--remote', required=True)
    upload.add_argument('--branch', default='gh-pages')
    upload.add_argument('--message', default='Publish module repository')
    pages = commands.add_parser('prepare-pages')
    pages.add_argument('--checkout', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'publish':
        publish(args.site, args.remote, args.branch, args.message)
    else:
        prepare_pages(args.checkout)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f'error: {error}', file=sys.stderr)
        sys.exit(1)
