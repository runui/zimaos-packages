# Writing a package

Create a lowercase package directory:

```text
packages/<name>/
├── package.json
├── build.sh                 Any recipe filename and supported interpreter
├── files/
│   └── usr/
├── NOTICE
└── tests/
```

`files/`, notices, and integration tests depend on the package's requirements.
No Meson fragment or Python callback is required inside a package.

## Manifest

| Field | Meaning |
| --- | --- |
| `name` | Package directory, module identity, and raw filename stem |
| `description` | Short English package description |
| `version` | Pinned upstream version or snapshot date |
| `release` | Positive integer packaging revision |
| `module_version` | SemVer module version, optionally prefixed with `v` |
| `architecture` | Upstream payload architecture for a package with a single `source` |
| `sources` | Architecture-keyed source records for a package supporting multiple architectures |
| `targets` | Supported names from `targets/` |
| `license`, `homepage` | Primary upstream license and project URL |
| `source.filename` / `sources.<architecture>.filename` | Original upstream archive basename |
| `source.url` / `sources.<architecture>.url` | HTTPS archive URL |
| `source.sha512` / `sources.<architecture>.sha512` | Lowercase pinned SHA-512 digest |
| `source.checksum_reference` / `sources.<architecture>.checksum_reference` | Optional upstream checksum reference |
| `source_date_epoch` | Fixed Unix timestamp for reproducible archives and images |
| `build.command` | Command and arguments as a JSON array |
| `build.inputs` | Package-relative files used by the command, including local helper scripts |
| `build.artifacts` | Optional additional artifact basenames |
| `services` | Optional module service declarations |
| `tests.smoke`, `tests.integration` | Optional test commands as arrays |

Additional fields may contain recipe-specific settings. The command receives the
manifest path and can interpret those fields itself. List all local recipe
inputs so Ninja can rebuild the package when a helper changes.

Use either `architecture` plus `source`, or `sources` keyed by architectures
such as `x86_64` and `aarch64`. The two forms cannot be combined. Each declared
target selects its architecture's pinned source; a missing architecture is an
error. The build command receives the selected archive through
`ZPKG_SOURCE_ARCHIVE`, while `ZPKG_MANIFEST` continues to point to the original
manifest.

A Shell recipe might declare:

```json
"build": {
  "command": ["sh", "build.sh"],
  "inputs": ["build.sh"],
  "artifacts": ["components.txt"]
}
```

A Python recipe can declare `["python3", "recipe.py"]`; an executable can declare
`["./build"]`. Commands execute directly, without implicit shell expansion, from
the package directory. They can invoke upstream Make, CMake, Meson, or other
build tools. Add the relevant tools to the build environment and CI image.

## Command interface

| Environment variable | Value |
| --- | --- |
| `DESTDIR` | Fresh staged root; install only under `DESTDIR/usr` |
| `SOURCE_DATE_EPOCH` | Fixed manifest timestamp |
| `ZPKG_SOURCE_ARCHIVE` | Verified upstream archive |
| `ZPKG_BUILD_DIR` | Persistent workspace inside the Meson build directory |
| `ZPKG_ARTIFACTS_DIR` | Fresh directory for declared additional artifacts |
| `ZPKG_DETAILS_FILE` | Optional JSON output for package-specific provenance |
| `ZPKG_MANIFEST` | Package manifest path |
| `ZPKG_PACKAGE_DIR` | Package source directory |
| `ZPKG_TARGET_NAME` | Selected target name |
| `ZPKG_TARGET_ARCH` | Payload architecture |
| `ZPKG_TARGET_JSON` | Serialized target definition |
| `ZPKG_TOOLS_DIR` | Optional repository helper directory |

A successful command must produce its staged files and every declared artifact.
Artifact basenames cannot collide with shared pipeline outputs. Intermediate
compiler files belong in `ZPKG_BUILD_DIR`, which is retained between staging
runs. The command controls its own source extraction, compiler, and toolchain;
the target definition does not configure a cross compiler automatically.

Python recipes may import helpers from `ZPKG_TOOLS_DIR`.
`common.extract_archive()` rejects traversal and escaping links, omits special
files, and converts absolute links to equivalent internal relative links. Its
`exclude_trees` argument can omit virtual filesystem contents in a rootfs.
Other languages can use their own extraction and compilation tools.

Do not embed host paths, proxy settings, or temporary directories in staged
files or provenance details. The shared pipeline preserves the environment's
proxy settings for downloads and package commands.

## Runtime payload and notices

`files/` is merged after the build command, during image assembly. Preserve
executable modes and symlinks. Changing an existing payload file rebuilds the
image without rerunning compilation. Reconfigure after adding or removing files.

Include required upstream license texts in the payload. The builder copies a
package's `NOTICE` into `usr/share/licenses/<name>/NOTICE` and the distribution
bundle. Provenance and component inventories do not replace upstream notices
or corresponding-source obligations.

## Tests

Test commands receive `--image PATH` appended to their declared arguments and
`ZPKG_TEST_WORK_DIR` pointing to a disposable-work area below the build directory.
They also receive `ZPKG_TARGET_NAME` and `ZPKG_TARGET_ARCH` for the selected target.
The test creates and removes its own temporary subdirectory. Generate fixtures
and signing keys at runtime; keep default tests independent of network contents
once the image is built.

Provide smoke coverage for package behavior. Declare privileged mount tests as
`integration`; they are registered only with `-Dintegration_tests=true`. Create
mounts in private namespaces and verify host mounts remain unchanged. Test
commands can use any language that implements this interface.

## Contributor workflow

```sh
meson setup build -Dpackages=PACKAGE_NAME -Dtarget=TARGET_NAME
meson compile -C build
meson test -C build --suite unit --suite smoke --print-errorlogs
meson compile -C build bundles
```

Use `meson configure build` to inspect options and `meson introspect build
--targets` to inspect the graph. Document kernel, tool, and API requirements in
the package guide. Builds select a target architecture and do not take a host
OS version.
