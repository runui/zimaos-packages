# Architecture

Meson defines the build graph and test suites. Ninja schedules its targets and
tracks their inputs and outputs. Shared Python tasks perform individual file
operations; they do not orchestrate a complete build or invoke other build
phases. Packages expose commands through their manifests.

## Build graph

Each selected package has independent targets:

```text
source specification -> verified download -> package staging
                                                |
                    runtime files + metadata ---+-> image assembly
                                                         |
                                                   SquashFS image
                                                         |
                         distribution files ---------> checksums
                                                         |
                                               distribution bundle
```

- **Download:** Fetch the pinned archive and verify SHA-512 before publishing it.
  Optional shared cache entries are addressed by checksum and filename.
- **Stage:** Verify the source again, run the package command with a fresh
  `DESTDIR`, and record a deterministic payload archive, details, and declared
  package artifacts. A persistent package work directory supports upstream
  incremental compilation.
- **Assemble:** Combine the staged payload with runtime files, extension/module
  metadata, license notices, and provenance into a deterministic image archive.
- **Image:** Create the read-only SquashFS raw image from the assembled archive.
- **Checksums:** Hash the declared distribution files.
- **Bundle:** Optionally archive distribution files and checksums for release.

Staging and image archives are real declared outputs, not marker files hiding
an untracked directory tree. Deleting an image causes Ninja to regenerate it
from the assembled archive without recompiling the package.

## Incremental behavior

Meson generates separate specifications for fetching, staging, assembly, image
creation, and testing. Unchanged specifications retain their timestamps.

| Change | Work required |
| --- | --- |
| No input changes | None |
| Existing runtime file or notice | Assembly, image, checksums, and any requested bundle |
| Recipe command input | Package staging and downstream targets |
| Upstream input identity | Download verification and downstream targets |
| Deleted raw image | Image and downstream targets |

Selected packages can build in parallel. Use Ninja's job count and the
`image_threads` option to control concurrency. Add or remove packages and
payload files through a Meson reconfiguration so the declared input set updates.
Different target architectures use separate build directories and can build
concurrently from the same checkout.

## Image contents

System extensions contain only `/usr`, including:

```text
usr/lib/extension-release.d/extension-release.<package>
usr/share/casaos/modules/<package>.json
usr/share/zimaos-packages/<package>/build-info.json
usr/share/licenses/<package>/LICENSE
usr/share/licenses/<package>/packaging/LICENSE
usr/share/licenses/<package>/packaging/NOTICE
```

The filename, compatibility-file suffix, and module identity use the package
name. Targets and provenance declare the supported OS and architecture. The
extension uses systemd's `ID=_any` with an explicit architecture
and no `VERSION_ID`, allowing a self-contained userspace to work across host
OS versions. systemd requires a matching `VERSION_ID` when a specific host
`ID` is used, so dropping that field alone would still reject the image.
Package runtime requirements remain separate from the repository URL, which
is scoped by target.

Public directories use `<os_id>/<architecture>/`, with architecture names such
as `amd64` and `arm64`. Each directory contains `mod-v2.json` and
`packages/<name>/<module_version>/<bundle_sha256>/` distributions. Internal
target names such as `zimaos-amd64` remain build identifiers. Each publication
replaces the branch contents with the current set of verified distributions.
Files smaller than GitHub's 100 MiB Git file limit are served by Pages. Files
at or above that limit use Git LFS on `gh-pages`, with exact paths recorded in
the generated `.gitattributes`. Publication conservatively requires each file
to be smaller than 2 GiB. The module `url`, `bundle_url`, and directory indexes
point to `https://media.githubusercontent.com/media/<owner>/<repo>/gh-pages/`
followed by the original publication path for LFS files. Raw images remain
complete files with their original names; `zpkg` needs no Git LFS support.

CI stages the complete publication tree in a disposable Git repository and
uploads LFS objects before pushing `gh-pages`. Failed uploads leave the remote
branch unchanged. Updates preserve branch history and reject concurrent branch
changes instead of forcing a push. Repeated identical publications do not create
commits. Replacing the published tree does not delete historical LFS objects;
storage and downloads use the repository's Git LFS allowance.

Pages deployment checks out `gh-pages` without downloading LFS content, removes
LFS pointer files and `.gitattributes`, and then checks the remaining site size.
The generated indexes keep links to the complete LFS downloads. Neither LFS
pointers nor browser redirect pages are served as package downloads.

The shared `tools/templates/directory.html` template renders an `index.html` in
every public directory. `tools/templates/404.html` renders the root error page.
Indexes contain relative links, UTC modification times,
and byte sizes; timestamps come from package source epochs for reproducible
output. GitHub Pages serves these static indexes directly and uses `404.html`
for missing paths without changing the error's HTTP status.

Provenance records the resolved manifest for the selected architecture, upstream
identity, recipe and payload hashes, target metadata, and optional package
details. Other architectures' source records do not affect an image's provenance
or clean build output. Published provenance uses repository-relative
input names; temporary machine paths remain in the local build configuration.

Images are read-only. Packages needing writable state define their own
initialization, persistence, upgrade, and shutdown behavior. Removing an image
must not implicitly remove persistent user data.

## Tests and distribution

The `unit` suite covers manifests, source integrity, safe archive extraction,
and real Meson/Ninja builds with independent Shell and Python recipes. It tests
incremental boundaries, changed compatibility metadata, missing outputs, corrupt
inputs, failure recovery, and release bundles.

Package `smoke` suites inspect and exercise built images. Opt-in `integration`
suites run privileged checks with disposable state and private mount namespaces.
Neither builds nor tests install extensions on the live host.

All generated files stay below the selected build directory, except an explicitly
configured shared source cache. CI runs the same Meson commands as contributors
and verifies distribution bundles before assembling the module repository.
To prepare large files, pass
`--lfs-base-url https://media.githubusercontent.com/media/<owner>/<repo>/gh-pages`
to `tools/repository.py build`. The output contains complete files and their
LFS attributes. Publish it with `tools/publication.py publish --site <directory>
--remote https://github.com/<owner>/<repo>.git`; CI supplies the repository from
its context and authenticates through `GITHUB_TOKEN` with `contents: write`.
Run `tools/publication.py prepare-pages --checkout <directory>` only on a
disposable checkout before deploying Pages. Shared tests require Git and Git
LFS and exercise uploads against temporary local repositories.
