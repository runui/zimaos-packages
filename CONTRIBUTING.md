# Contributing

Keep each package self-contained under `packages/<name>/`. Meson owns the build
graph; shared tasks handle extension packaging. Upstream-specific compilation,
configuration, runtime files, notices, and tests belong in the package directory.

## Adding or updating a package

1. Follow the [command interface](docs/packaging.md). Any recipe language is
   welcome. Declare command inputs and additional outputs in the manifest.
2. Pin upstream archives by HTTPS URL and SHA-512. Preserve their identity,
   notices, and source references.
3. Declare supported targets explicitly. Build with the destination OS version,
   without inferring compatibility from the build machine.
4. Increment `release` for packaging changes. Update `version` when the upstream
   input changes and keep `module_version` consistent with the module.
5. Provide meaningful smoke tests and, when needed, isolated integration tests.
   Run the shared `unit` suite and affected package suites through Meson.
6. Reconfigure Meson after adding or removing packages or payload files. Verify
   that an unchanged build is a no-op and that relevant changes rebuild their
   dependents.
7. Keep generated images, downloads, compiler workspaces, test keys, and logs
   below the build directory. Remove it after local validation when a clean
   source checkout is required.

## Runtime integration

Images contain only `/usr`. Packages needing writable state must define an
explicit persistence and lifecycle design. Preserve upstream command behavior,
existing host files, and independently managed software. Do not enable
host-wide writable layers as an implicit installation side effect.

Builds and tests must not install, refresh, or remove live extensions. Mount
tests use private namespaces and disposable state. Keep tests independent of
developer usernames, paths, proxies, installed applications, and external
repository contents. Generated fixtures and signing keys must be cleaned up.

## Documentation and distribution

Write public documentation, comments, and metadata in English. Document stable
interfaces and supported behavior. Keep personal setup notes, investigation
transcripts, experiments, and one-off validation reports out of the repository.

Preserve provenance and checksums with distributed artifacts. Include upstream
copyright and license notices and meet corresponding-source requirements.
Prebuilt binary archives are not corresponding source; keep component-specific
license and source information with each package.
