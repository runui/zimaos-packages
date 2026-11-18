# OCI mirror proxy on ZimaOS

This module runs the official [oci-mirror-proxy](https://github.com/runui/oci-mirror-proxy)
Linux binary as a system service. It automatically initializes configuration,
downloads the upstream default mirror catalog, and connects local Docker Engine.
It supports ZimaOS on x86_64 and ARM64, with both containerd and classic Docker
image stores. No Go toolchain or Alpine environment is needed at runtime.

## Install and use

Follow the [repository configuration guide](../../docs/user-guide.md), then run:

```sh
sudo zpkg install oci-mirror-proxy
oci-mirror-proxy version
oci-mirror-proxy status
docker pull hello-world
```

The module manager starts `zimaos-oci-mirror-proxy.service`. On its first start,
the service calls upstream `init`, `remote update`, and `dockerd apply --verify`.
Upstream merges and backs up Docker configuration. The proxy becomes the
preferred mirror while existing mirrors and unrelated settings are preserved.
Classic Docker is reloaded with SIGHUP when needed; containerd reads its registry
configuration directly. The automatic flow does not restart Docker.

Initial setup requires network access to the upstream catalog and a running
local Docker Engine at `/var/run/docker.sock`. Mirror availability and speed
depend on your network. Catalog download failures retain saved settings; a failed
first-run download is retried on the next service start. Docker integration
errors are logged without stopping an otherwise healthy proxy, matching the
upstream installer's recovery behavior.

The proxy listens on `127.0.0.1:24817`. The registry API has no authentication;
keep it on loopback. The standard upstream administration socket is
`/run/oci-mirror-proxy/admin.sock`. Upstream makes that system socket accessible
to local users, so ordinary status and upstream-management commands work without
sudo. Local users can change proxy settings through that API.

Stop an independently installed proxy before installing this module: both would
use the same default listener and admin socket. This module has its own service
name and does not replace `/usr/local/bin/oci-mirror-proxy`, upstream service
definitions, or `/etc/oci-mirror-proxy`. Check `command -v oci-mirror-proxy` if a
separate installation is also in your PATH; this module's executable is
`/usr/bin/oci-mirror-proxy`.

## Configuration and subscriptions

The writable configuration is
`/DATA/.zimaos-packages/oci-mirror-proxy/config.yaml`. Its sibling `config.d/`
holds upstream subscription fragments and local overrides. Edit the YAML as
root and run `oci-mirror-proxy reload`, or use the upstream CLI:

```sh
oci-mirror-proxy upstream list
oci-mirror-proxy remote list
oci-mirror-proxy remote update --all
```

The first catalog update uses upstream's default catalog URL and `global`
region. To change the region, use the upstream interface, for example:

```sh
oci-mirror-proxy remote update --regions cn
```

Later service starts and module upgrades call `remote update --all`, preserving
each subscription's region selection, local disabled choices, and manual
upstreams. Once the initial download succeeds, removing all subscriptions does
not cause the package to add the default subscription again. The first-run
`.bootstrap-remote` marker is retained only while that initial download is pending.

The package ships no mirror list or configuration schema of its own. Initialization,
subscription validation, safe writes, Docker store detection, merging, backups,
and activation remain upstream behavior. Upgrading the pinned binary brings
upstream changes to those commands; package smoke tests exercise their lifecycle
together before publication.

See the [upstream configuration reference](https://ocimp.zmide.com/docs/configuration)
for authentication, extra registries, timeouts, and advanced settings. The full
release example is installed at
`/usr/share/doc/oci-mirror-proxy/config.example.yaml`.

## Update

```sh
sudo zpkg update
sudo zpkg upgrade oci-mirror-proxy
oci-mirror-proxy version
oci-mirror-proxy status
```

Use `zpkg` to update the binary and service. The upstream shell installer writes
outside the system extension and is not the update mechanism for this module.
Use `remote update --all` to refresh mirror subscriptions independently of a
binary release. Service restarts also refresh subscriptions and reapply Docker
integration without replacing saved YAML or subscription selections.

## Stop, remove, and recover

```sh
sudo systemctl stop zimaos-oci-mirror-proxy.service
sudo zpkg remove oci-mirror-proxy
```

Stopping the service, including during module updates and removal, calls
upstream `dockerd revert` against current Docker files. It removes this proxy's
entries while preserving unrelated settings and subsequent edits. Startup
ordering keeps Docker available until cleanup finishes during normal shutdown.
The service saves upstream `server info --json` before applying Docker settings,
so cleanup can run after the proxy exits or crashes.
If a stop or power loss leaves a recovery snapshot, the next start retries its
cleanup before creating a new Docker connection. A failed cleanup keeps that
snapshot and prevents startup from overwriting it.

Configuration, subscription metadata, and upstream backups under
`/DATA/.zimaos-packages/oci-mirror-proxy/` survive reboot, updates, and removal.
Reinstalling reuses that state. Back up the whole directory while the service
is stopped. Stop the service and move that directory aside to start fresh.

Inspect setup or cleanup errors with:

```sh
sudo systemctl status zimaos-oci-mirror-proxy.service
sudo journalctl -u zimaos-oci-mirror-proxy.service -b
sudo oci-mirror-proxy dockerd plan
sudo oci-mirror-proxy doctor
```

If initial Docker integration failed, resolve the reported issue and restart
the package service to retry:

```sh
sudo systemctl restart zimaos-oci-mirror-proxy.service
```

For a manual apply while the service is running, keep upstream backups in the
package's persistent directory:

```sh
sudo env OCI_MIRROR_PROXY_CONFIG_HOME=/DATA/.zimaos-packages/oci-mirror-proxy \
  /usr/bin/oci-mirror-proxy dockerd apply --verify
```

If cleanup fails, systemd reports the failure and retains `integration.json`.
Service stop failure cannot prevent the module manager from removing an image;
resolve the error and retry cleanup **before removal**, while the packaged CLI
is still available:

```sh
sudo /usr/libexec/zimaos-packages/oci-mirror-proxy-lifecycle disconnect
```

If Docker is unavailable, upstream can generate cleanup offline. Select its
actual image store (`containerd` or `legacy`):

```sh
sudo /usr/libexec/zimaos-packages/oci-mirror-proxy-lifecycle disconnect \
  --store containerd --no-reload
```

For `legacy`, activate saved Docker configuration when Docker is available again.
Offline cleanup does not prove that a running daemon loaded the change. Keep
persistent state and backups until recovery is complete.

## Build and validation

Build in a disposable directory with `-Dpackages=oci-mirror-proxy` and either
`-Dtarget=zimaos-amd64` or `-Dtarget=zimaos-arm64`; follow the shared
[packaging workflow](../../docs/packaging.md). The recipe pins official release
archives by SHA-512, installs the executable unchanged, and includes upstream
and bundled dependency licenses.

License texts are maintained in the package's `licenses/` source directory and
installed under `/usr/share/licenses/oci-mirror-proxy/`. The main project keeps
its upstream `LICENSE`; bundled components keep their own license and notice
files under `third-party/<component>/`. The packaging code's license is
`packaging/LICENSE`. All texts ship with the extension.

The smoke suite uses the built image, a temporary proxy process, a local catalog
and registry, and a disposable Docker API. All Docker paths are redirected into
the test directory and signaling is disabled. It checks first-run setup,
subscription refresh, configuration preservation, Docker merging and cleanup,
failure recovery, and reuse after removal without installing the extension or
changing host Docker settings. Runtime tests run on the target architecture;
cross builds and image inspection do not execute the target binary.
