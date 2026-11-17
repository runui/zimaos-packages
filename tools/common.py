"""File and archive helpers shared by extension tasks and package scripts."""

import copy
import hashlib
import json
import os
from pathlib import Path
import posixpath
import subprocess
import tarfile
import tempfile


def run(*arguments, **kwargs):
    command = list(map(str, arguments))
    print('+', ' '.join(command), flush=True)
    return subprocess.run(command, check=True, **kwargs)


def digest(path, algorithm='sha256'):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest()


def replace_if_changed(temporary, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and digest(temporary) == digest(destination):
        temporary.unlink()
    else:
        os.replace(temporary, destination)


def write_changed(path, data):
    path = Path(path)
    if isinstance(data, str):
        data = data.encode()
    if path.is_file() and path.read_bytes() == data:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(data)
    temporary.chmod(0o644)
    replace_if_changed(temporary, path)


def write_json(path, value):
    write_changed(path, json.dumps(value, indent=2, sort_keys=True) + '\n')


def extract_archive(archive, destination, *, exclude_trees=()):
    """Extract within destination, preserving internal root-relative links."""
    destination.mkdir(parents=True)

    def filter_member(member, path):
        name = member.name.removeprefix('./')
        parts = name.split('/')
        if parts[0] in exclude_trees and len(parts) > 1 and any(parts[1:]):
            return None
        if not (member.isdir() or member.isfile() or member.issym() or member.islnk()):
            return None
        if member.issym() and member.linkname.startswith('/'):
            member = copy.copy(member)
            member.linkname = posixpath.relpath(member.linkname.lstrip('/'), posixpath.dirname(name) or '.')
        return tarfile.data_filter(member, path)

    with tarfile.open(archive) as stream:
        stream.extractall(destination, filter=filter_member)


def pack_tree(root, destination, epoch):
    """Write a deterministic staging archive with root ownership."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        with tarfile.open(temporary, 'w', format=tarfile.PAX_FORMAT) as archive:
            for path in sorted(root.rglob('*')):
                member = archive.gettarinfo(str(path), str(path.relative_to(root)))
                member.uid = member.gid = 0
                member.uname = member.gname = ''
                member.mtime = int(epoch)
                member.pax_headers = {}
                if member.isfile():
                    with path.open('rb') as content:
                        archive.addfile(member, content)
                else:
                    archive.addfile(member)
        temporary.chmod(0o644)
        replace_if_changed(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
