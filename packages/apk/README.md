# Using apk on ZimaOS

The `apk` module supplies official Alpine package tools and a persistent Alpine
userspace. Install it through `zpkg` after following the
[repository configuration guide](../../docs/user-guide.md).

The environment supplies its own Alpine userspace and does not require an
exact ZimaOS version. It requires writable `/DATA` storage, OverlayFS, mount
namespaces, and the host mount and chroot tools used by its service. Use the
image for your device's architecture.

## Install Alpine software

```sh
sudo apk update
sudo apk add curl
sudo apk exec curl --version
sudo apk info
```

Use `sudo apk exec COMMAND ...` to run installed Alpine programs. This also
works when ZimaOS already has a command with the same name. The module preserves
existing host commands and independently managed files. Programs that have no
host name conflict can also receive launchers in `/DATA/.local/bin`; that
directory must be in your shell's `PATH` to discover them automatically.

Open an Alpine shell when you need several commands:

```sh
sudo apk shell
```

Type `exit` to leave. Normal `apk` package operations execute in this Alpine
environment. Host data directories, your home, and your working directory are
accessible to commands running there. These commands run as root and can change
those files; the environment is not a security sandbox.

To update Alpine software or remove a package:

```sh
sudo apk upgrade
sudo apk del curl
```

The base is a pinned Alpine **edge** snapshot. Its default online repositories
are Alpine edge `main` and `community`, which change independently of the
published ZimaOS module. An `apk upgrade` updates installed Alpine packages; it
does not update the ZimaOS module or host OS.

## Inspect and change Alpine settings

```sh
sudo apk environment
sudo apk exec cat /etc/apk/repositories
sudo apk shell
```

Inside that shell, edit `/etc/apk/repositories` with `vi` if you need another
Alpine mirror. Keep repositories compatible with the environment's Alpine
release; mixing stable and edge repositories can break dependencies. Leave the
official signing keys and signature verification enabled. Run `apk update`
after changing repositories, then `exit` to return to the host shell.

Alpine settings inside the environment persist. Its DNS resolver follows the
host configuration automatically, so make persistent DNS changes through
ZimaOS network settings.

The extension also supports `apk exec`, `apk shell`, and `apk environment`.
Explicit upstream `--root` / `-p` operations retain host path semantics.
Standalone package tools such as `mkpkg`, `mkndx`, and `verify` use the official
client with host paths. Use the ordinary environment commands above for normal
software installation.

## Persistence and lifecycle

| Location | Purpose | Survives reboot and module removal? |
| --- | --- | --- |
| `/usr/share/zimaos-packages/apk/base` | Read-only Alpine base supplied by the extension | Supplied again by a compatible installed image |
| `/DATA/.zimaos-packages/apk` | Installed packages, package database, Alpine configuration, and OverlayFS state | Yes |
| `/run/zimaos-packages/apk` | Temporary mounts and runtime coordination | No; recreated as needed |
| `/DATA/.local/bin` | Launchers managed by the extension alongside independently managed files | Managed launchers are removed when the module stops and recreated on initialization |

The `apk-environment.service` initializes the environment at module startup.
Package operations also initialize it when needed. An image update remounts
the read-only base while preserving the writable Alpine state. A change to a
different distribution requires a separate migration; changing repository lines
does not migrate the package database.

`sudo zpkg remove apk` removes the extension and stops its service. It keeps
`/DATA/.zimaos-packages/apk`. Reinstalling a compatible extension reuses those
packages and settings. Existing host files and command launchers owned by
other software are preserved. Do not delete the whole `/DATA/.local/bin`
directory when cleaning up this module.

## Back up and restore

Finish all commands using the environment first. Stop the environment and
avoid starting new `apk` commands while taking the backup. Choose an unused
backup filename:

```sh
sudo systemctl stop apk-environment.service
sudo tar -C /DATA/.zimaos-packages -czf /DATA/apk-state-backup.tar.gz apk
sudo systemctl start apk-environment.service
```

Copy the backup to another device if you need protection against disk failure.
It contains the writable state; restoring it requires a compatible `apk`
extension and Alpine base.
The backup must also match the device's architecture; installed x86_64 and
ARM64 packages cannot share the same writable environment.

To restore on a compatible installation, stop the service and keep the current
state as a separate backup. Choose an unused name for that backup too:

```sh
sudo systemctl stop apk-environment.service
sudo mv /DATA/.zimaos-packages/apk /DATA/.zimaos-packages/apk.before-restore
sudo tar -C /DATA/.zimaos-packages -xzf /DATA/apk-state-backup.tar.gz
sudo systemctl start apk-environment.service
sudo apk environment
```

If there is no existing state directory, skip the `mv` command. If you want a
fresh environment, stop the service and move the state aside in the same way,
then start it again without extracting a backup. Keep the old directory until
you are sure you no longer need its data.
