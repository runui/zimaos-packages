# zimaos-packages

Package recipes for ZimaOS system extensions, built with Meson and Ninja. Each
package produces a read-only `.raw` image with explicit OS and architecture
compatibility metadata. Package scripts can use their own build tools and
languages.

## Requirements

Build on Linux with Meson 1.3+, Ninja, Python 3.12+, and squashfs-tools. Packages
may require additional tools. Runtime tests also use binutils, OpenSSH client
tools, and private mount namespaces.

On Debian or Ubuntu:

```sh
sudo apt-get install meson ninja-build python3 squashfs-tools binutils openssh-client util-linux
```

## Build

List the package catalog:

```sh
python3 tools/configure.py list
```

Select packages, a definition from `targets/`, and the destination system's exact
`VERSION_ID`. Replace the uppercase placeholders with those values:

```sh
meson setup build -Dpackages=PACKAGE_NAME -Dtarget=TARGET_NAME -Dos_version=VERSION_ID
meson compile -C build
```

Use a comma-separated package list or `-Dpackages=all` for multiple packages.
Each build directory holds one target and OS version configuration. Meson
registers the dependencies; Ninja handles parallel execution and incremental
rebuilds.

Useful targets:

```sh
meson compile -C build list      # Show available packages
meson compile -C build sources   # Fetch and verify selected upstream inputs
meson compile -C build bundles      # Create distribution bundles
```

Sources are pinned by HTTPS URL and SHA-512. Fetching inherits standard proxy
environment variables. Downloads stay in the build directory; an optional
absolute `-Dsource_cache=...` directory can share verified inputs across builds.

## Artifacts

The build directory contains `<package>.raw`, `<package>.module.json`,
`<package>.build-info.json`, `<package>.SHA256SUMS`, the upstream archive, and
recipe-declared artifacts. `bundles` produces `<package>.bundle.tar.gz` containing
these distribution files and notices. Intermediate staging archives and local
configuration files are excluded from the bundle.

After extracting a bundle, verify its `<package>.SHA256SUMS`. On a compatible
ZimaOS system, install the raw image with:

```sh
zpkg install /path/to/package.raw
```

Compatibility labels describe the destination system. Changing a label does
not establish compatibility with another OS release or architecture.

## Tests

```sh
meson test -C build --suite unit --print-errorlogs
meson test -C build --suite smoke --print-errorlogs
```

Privileged integration suites are opt-in:

```sh
meson configure build -Dintegration_tests=true
sudo meson test -C build --suite integration --print-errorlogs
```

To run shared tooling checks without selecting packages:

```sh
meson setup build/check
meson test -C build/check --suite unit --print-errorlogs
```

Tests generate disposable inputs and signing keys. Integration tests create
mounts only in private namespaces and leave live extensions and persistent
package data untouched.

## Maintenance

Existing recipe and payload file changes rebuild their dependent targets.
Reconfigure after adding or removing packages or payload files:

```sh
meson setup --reconfigure build
```

`meson compile -C build --clean` removes declared generated outputs while keeping
configuration and package workspaces. Remove the build directory for a complete
cleanup. An explicitly configured external source cache is managed separately.

| Directory | Purpose |
| --- | --- |
| `packages/<name>/` | Manifest, build command, runtime files, notices, and tests |
| `targets/` | OS and architecture definitions |
| `tools/` | Manifest readers and individual packaging tasks |
| `tests/` | Shared integrity and build-graph tests |
| `docs/` | Architecture and package interface documentation |

See [Architecture](docs/architecture.md), [Writing a package](docs/packaging.md),
and [Contributing](CONTRIBUTING.md). CI discovers package/target combinations
from manifests. Pull requests use a synthetic OS version; manual artifact builds
require an explicit destination OS version.

## License

Repository tooling uses the [MIT License](LICENSE). Packaged software retains
its upstream licenses, documented in package manifests and notices.
