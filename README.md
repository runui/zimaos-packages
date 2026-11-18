# zimaos-packages

A community module repository for ZimaOS. Packages are distributed as read-only
system extension images and installed with ZimaOS's built-in `zpkg` command.

Start with the [user configuration guide](docs/user-guide.md) to add this
repository, install packages, manage updates, and remove the repository. No
local build tools are needed.

| Package | Purpose | User instructions |
| --- | --- | --- |
| `apk` | Official Alpine package tools with a persistent Alpine userspace | [Using apk](packages/apk/README.md) |

The repository targets **ZimaOS on x86_64 and ARM64**. Choose the repository for
your device's architecture. Repository URLs stay the same across ZimaOS
upgrades; package compatibility depends on the package's actual runtime
requirements.

Choose the module store for your device's architecture:

| Architecture from `uname -m` | Module store |
| --- | --- |
| `x86_64` | [zimaos/amd64](https://runui.github.io/zimaos-packages/zimaos/amd64/mod-v2.json) |
| `aarch64` | [zimaos/arm64](https://runui.github.io/zimaos-packages/zimaos/arm64/mod-v2.json) |

Downloads and declarations become available after a successful GitHub Pages
deployment. The publication branch contains package distributions, catalogs for
each architecture, checksums, and provenance. Source code stays on `main`.
The [download directory](https://runui.github.io/zimaos-packages/) lists every
published folder and file with its modification time in UTC and size in bytes.

Contributors can read [Architecture](docs/architecture.md),
[Writing a package](docs/packaging.md), and [Contributing](CONTRIBUTING.md).

Repository tooling uses the [MIT License](LICENSE). Packaged software retains
its upstream licenses, documented in package manifests and notices.
