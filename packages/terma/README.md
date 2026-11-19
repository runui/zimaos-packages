# Terma on ZimaOS

This module runs [Terma](https://github.com/zmide/Terma) 1.8.1 in Web mode for
SSH terminals, SFTP, connection management, browser VNC, and port forwarding.
It includes its own Node.js 22.23.5 runtime and production dependencies. No
separate Node.js, npm, Alpine environment, or container is needed at runtime.
The module supports ZimaOS on x86_64 and ARM64 with glibc 2.34 or newer and
libstdc++ providing `GLIBCXX_3.4.29`. An OpenSSH client (`ssh`) must be available
for upstream's system SSH fallback. Desktop Electron, native desktop RDP/X11
viewers, and a local graphical desktop are not included.

## Install and first access

Follow the [repository configuration guide](../../docs/user-guide.md), then run:

```sh
sudo zpkg install terma
systemctl status zimaos-terma.service
```

The module manager starts `zimaos-terma.service` and adds a **Terma** dashboard
tile. The tile redirects to the upstream Web UI at `http://NAS_ADDRESS:8088/`
in a new tab, using the same hostname as the dashboard. It uses the upstream
Terma icon bundled with the pinned release.

The managed service sets `TUNNEL_WEB_HOST=0.0.0.0` for LAN access and uses the
upstream default port, `8088`. Upstream requires a configured Web password or
access token for remote requests. For initial password setup, forward the
loopback port through SSH from your own computer (replace `USER` and
`NAS_ADDRESS`):

```sh
ssh -N -L 8088:127.0.0.1:8088 USER@NAS_ADDRESS
```

Open `http://127.0.0.1:8088` on that computer. Under **Settings → Security**, set
a Web password and select authentication for all access if other local users
should also authenticate. Once the password is set, use the dashboard tile or
`http://NAS_ADDRESS:8088` for LAN access.
HTTPS can be provided by your own reverse proxy; configure Terma's allowed
hosts, trusted proxy, and origin protocol settings to match that deployment.

As upstream permits direct loopback access during initial setup, local users
on the NAS can access that interface until authentication for all access is
selected. The service runs as root, consistent with the module manager's system
services. Its local file operations and terminal processes therefore have root
privileges. Grant Web access only to trusted NAS administrators. The service
uses a private home directory and managed SSH directory rather than root's
normal home and SSH keys.

If port 8088 is occupied, upstream may select another port. Check
`/DATA/.zimaos-packages/terma/data/web.url` or the journal for the actual URL.
Stop an independently managed Terma service before using the same listener or
state. This module does not discover, stop, migrate, or remove other installs.

## Settings and persistence

The package stores writable state under `/DATA/.zimaos-packages/terma/`:

| Location | Contents |
| --- | --- |
| `data/` | SQLite database, security settings, runtime settings, snapshots, logs, and transfer state |
| `ssh/` | Terma-managed SSH keys |
| `home/` | Service home, including any system SSH client files |
| `environment` | Optional systemd environment file supplied by the administrator |

Terma creates its data and key directories with private permissions. The
service uses a `0077` umask. Runtime settings saved in the UI survive service
restarts, reboots, package updates, and removal. The extension's `/usr` files
are read-only; keep the packaged data and key paths rather than using the
upstream storage relocation UI, which needs to write beside the application.

The service's `TUNNEL_WEB_HOST=0.0.0.0` setting takes precedence over a listen
address saved in the UI. To restrict the service to loopback, create the optional
`environment` file with systemd environment syntax:

```ini
TUNNEL_WEB_HOST=127.0.0.1
TUNNEL_WEB_PORT=8088
```

Restart the service after editing this file. Environment overrides take
precedence over the service defaults and saved listener settings. The dashboard
tile uses port `8088`; if you change the port or restrict access to loopback,
use your configured URL or SSH tunnel instead. Upstream environment variables
such as `TERMA_AUTH_TOKEN` and `TERMA_RESET_WEB_ACCESS` are also supported;
protect this file with mode `0600`, and remove a reset variable after recovery.
Package defaults disable the automatic startup update check. Install application
and private runtime updates through `zpkg`; the upstream in-app installer cannot
replace files in the extension.

The `terma` command starts the upstream server in the foreground and passes
arguments and signals through unchanged. Stop the service before running it
against the package's normal state. For a separate instance, set both
`TERMA_DATA_DIR` and `TERMA_SSH_DIR` to your own writable directories and choose
a different `--port`. Normal invocations retain the caller's home directory;
only the system service uses the private service home.

```sh
sudo systemctl restart zimaos-terma.service
journalctl -u zimaos-terma.service -n 100 --no-pager
```

## Backup, update, and remove

Stop the service before copying the entire state directory so the SQLite
database, settings, and keys are backed up together. Backups contain credentials
and should be kept private. Restart the service after copying. To restore, stop
the service, restore the directory with its ownership and permissions preserved,
and start the service again. Save a backup before updating because upstream may
migrate its database format.

```sh
sudo zpkg update
sudo zpkg upgrade terma
sudo zpkg remove terma
```

Removal stops the managed service and removes the extension. It retains
`/DATA/.zimaos-packages/terma/`, including credentials and SSH keys, for later
reinstallation or manual recovery. Delete that exact directory yourself only
when its data is no longer needed. No host-wide Node.js executable, npm packages,
SSH configuration, or independently installed Terma files are managed by this
package.

## Build and verify

Build on Linux hardware matching the selected target. Alongside the repository
build tools, install a C/C++ compiler, Make, and binutils (`build-essential` and
`binutils` on Ubuntu). The recipe verifies the pinned Terma and Node.js archives,
uses the upstream dependency lock's SHA-512 integrity values, and builds the
PTY and serial-port bindings against the private Node.js headers. Dependency
install scripts are disabled. The upstream SSH2 patch and ZMODEM preparation
scripts are applied as in the upstream Web build. No Electron tools are used.

```sh
meson setup build -Dpackages=terma -Dtarget=zimaos-amd64
meson compile -C build
meson test -C build --suite unit --suite smoke --print-errorlogs
meson compile -C build bundles
```

Use `zimaos-arm64` on ARM64 hardware. Smoke tests extract the image and run the
private runtime, PTY, browser assets, authenticated API, and database restart
checks with disposable state. They also verify the dashboard declaration,
redirect, icon, and service listener configuration. They neither mount nor
install the extension.
Native binaries are checked for target architecture, runtime library versions,
and embedded library search paths. The distribution also includes the original
Terma source release, `terma.runtime-packages.json`, and
`terma.runtime-sources.tar` with the verified Node.js archive and dependency
tarballs. See [NOTICE](NOTICE) for licensing and corresponding source details.
