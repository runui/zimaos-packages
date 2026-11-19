# zimaos-packages

A community module repository for ZimaOS. Packages are distributed as read-only
system extension images and installed with ZimaOS's built-in `zpkg` command.

Start with the [user configuration guide](docs/user-guide.md) to add this
repository, install packages, manage updates, and remove the repository. No
local build tools are needed.

| Package | Purpose | User instructions |
| --- | --- | --- |
| `apk` | Official Alpine package tools with a persistent Alpine userspace | [Using apk](packages/apk/README.md) |
| `oci-mirror-proxy` | OCI registry mirror proxy with automatic local Docker integration | [Using the proxy](packages/oci-mirror-proxy/README.md) |
| `terma` | Self-hosted Web workspace for SSH, terminals, SFTP, VNC, and port forwarding | [Using Terma](packages/terma/README.md) |

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
deployment. The publication branch contains catalogs for each architecture,
checksums, provenance, and distribution files. Files of 100 MiB or more use Git
LFS on the publication branch and direct GitHub downloads. Source code stays on
`main`.
The [download directory](https://runui.github.io/zimaos-packages/) lists every
published folder and file with its modification time in UTC and size in bytes.

Contributors can read [Architecture](docs/architecture.md),
[Writing a package](docs/packaging.md), and [Contributing](CONTRIBUTING.md).

Repository tooling uses the [MIT License](LICENSE). Packaged software retains
its upstream licenses, documented in package manifests and notices.
