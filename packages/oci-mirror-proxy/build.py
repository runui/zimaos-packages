"""Package the unmodified, architecture-specific upstream Linux release."""

import json
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile

sys.path.insert(0, os.environ['ZPKG_TOOLS_DIR'])
from common import digest, extract_archive, write_json


def main():
    package = json.loads(Path(os.environ['ZPKG_MANIFEST']).read_text())
    with tempfile.TemporaryDirectory(dir=os.environ['ZPKG_BUILD_DIR']) as temporary:
        release = Path(temporary) / 'release'
        extract_archive(Path(os.environ['ZPKG_SOURCE_ARCHIVE']), release)
        binary = release / 'oci-mirror-proxy'
        header = binary.read_bytes()[:20]
        machine = {'x86_64': 62, 'aarch64': 183}[os.environ['ZPKG_TARGET_ARCH']]
        if (header[:6] != b'\x7fELF\x02\x01' or
                struct.unpack_from('<H', header, 18)[0] != machine):
            raise ValueError('release binary does not match the selected Linux architecture')
        destination = Path(os.environ['DESTDIR']) / 'usr/bin/oci-mirror-proxy'
        destination.parent.mkdir(parents=True)
        shutil.copyfile(binary, destination)
        destination.chmod(0o755)
        examples = Path(os.environ['DESTDIR']) / 'usr/share/doc/oci-mirror-proxy'
        examples.mkdir(parents=True)
        for name in ('README.md', 'config.example.yaml'):
            shutil.copyfile(release / name, examples / name)
        write_json(Path(os.environ['ZPKG_DETAILS_FILE']), {
            'compiled_locally': False,
            'upstream_revision': package['upstream_revision'],
            'binary_sha256': digest(binary),
        })


if __name__ == '__main__':
    main()
