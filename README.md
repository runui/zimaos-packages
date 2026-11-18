# zimaos-packages

A community module repository for ZimaOS. Packages are distributed as read-only
system extension images and installed with ZimaOS's built-in `zpkg` command.

Start with the [user configuration guide](docs/user-guide.md) to add this
repository, install packages, manage updates, and remove the repository. No
local build tools are needed.

| Package | Purpose | User instructions |
| --- | --- | --- |
| `apk` | Official Alpine package tools with a persistent Alpine userspace | [Using apk](packages/apk/README.md) |

The configured release currently supports **ZimaOS `1.8.1-alpha2` on x86_64**.
Images declare an exact OS version and architecture. Use the matching repository
URL from the user guide; another release or architecture needs its own published
images.

The module store entry point for this compatibility is:

```text
https://runui.github.io/zimaos-packages/zimaos-amd64/1.8.1-alpha2/mod-v2.json
```

Downloads and declarations become available after a successful GitHub Pages
deployment. The publication branch contains package distributions, compatibility
indexes, checksums, and provenance. Source code stays on `main`.

Contributors can read [Architecture](docs/architecture.md),
[Writing a package](docs/packaging.md), and [Contributing](CONTRIBUTING.md).

Repository tooling uses the [MIT License](LICENSE). Packaged software retains
its upstream licenses, documented in package manifests and notices.
