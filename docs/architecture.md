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
| Destination OS version | Assembly and downstream targets |
| Deleted raw image | Image and downstream targets |

Selected packages can build in parallel. Use Ninja's job count and the
`image_threads` option to control concurrency. Add or remove packages and
payload files through a Meson reconfiguration so the declared input set updates.

## Image contents

System extensions contain only `/usr`, including:

```text
usr/lib/extension-release.d/extension-release.<package>
usr/share/casaos/modules/<package>.json
usr/share/zimaos-packages/<package>/build-info.json
usr/share/licenses/zimaos-packages/LICENSE
```

The filename, compatibility-file suffix, and module identity use the package
name. Compatibility declares an exact OS ID, OS version, and architecture.
Provenance records upstream identity, recipe and payload hashes, target metadata,
and optional package details. Published provenance uses repository-relative
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
and retains only distribution bundles from manually requested artifact builds.
