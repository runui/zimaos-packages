# User configuration guide

This guide explains how to use the published ZimaOS package repository. You need
a ZimaOS device, an administrator account with SSH access, and an Internet
connection. ZimaOS already provides `zpkg`; you do not need to install a package
manager or compile this project.

This is a community repository. Keep your existing module sources and choose
the source that matches your device's OS version and architecture.

## 1. Connect and check compatibility

Enable SSH in your device's ZimaOS settings, then open a terminal on your
computer. Replace the placeholders with your ZimaOS account and device address:

```sh
ssh YOUR_USER@YOUR_ZIMAOS_ADDRESS
```

Run these commands on the ZimaOS device:

```sh
cat /etc/os-release
uname -m
zpkg version
```

Find `ID` and `VERSION_ID` in the first command's output. Use the exact
`VERSION_ID`, including any suffix such as `-alpha2`.

| OS ID | VERSION_ID | Architecture from `uname -m` | Repository target |
| --- | --- | --- | --- |
| `zimaos` | `1.8.1-alpha2` | `x86_64` | `zimaos-amd64` |

The repository URL for that combination is:

```text
https://runui.github.io/zimaos-packages/zimaos-amd64/1.8.1-alpha2/mod-v2.json
```

Use the [published repository information](https://runui.github.io/zimaos-packages/repository.json)
to check available combinations and packages. The above endpoints appear after
the first successful deployment. If your OS version or architecture is absent,
wait for compatible packages. Renaming an image or forcing installation does
not make it compatible.

The root `mod-v2.json` points to the repository's default compatibility, which
can change over time. Use the version-specific URL above to keep your device
on its own OS version. `mod.json` is also published for older clients and
contains the same entries for each compatibility.

## 2. Back up your module configuration

The module repository settings are stored in:

```text
/etc/casaos/mod-management.conf
```

Make a backup before editing. If a backup with this name already exists, choose
another filename so you preserve your previous backup:

```sh
sudo cp -p /etc/casaos/mod-management.conf /etc/casaos/mod-management.conf.before-zimaos-packages
sudo vi /etc/casaos/mod-management.conf
```

In `vi`, press `i` to edit. Find the existing `[server]` section and add this
line inside it:

```ini
modstore = https://runui.github.io/zimaos-packages/zimaos-amd64/1.8.1-alpha2/mod-v2.json
```

Keep every existing setting and `modstore` line. Multiple repositories use
repeated `modstore` keys. If there is no `[server]` section, add one at the end
of the file, followed by the new line:

```ini
[server]
modstore = https://runui.github.io/zimaos-packages/zimaos-amd64/1.8.1-alpha2/mod-v2.json
```

Do not add a second `[server]` section if one already exists. Press `Esc`, type
`:wq`, and press Enter to save. This changes the module store settings; Docker
app store settings are separate.

## 3. Reload and check the package list

Reload the ZimaOS service so the module manager reads the edited configuration.
This can briefly interrupt the dashboard connection:

```sh
sudo systemctl restart zimaos.service
sudo zpkg update
sudo zpkg list-remote
```

Wait for the service to start before running `zpkg update`. The remote list
should contain `apk` and its published module version. Refreshing the list does
not upgrade installed packages.

The built-in official store is loaded by ZimaOS automatically. Existing custom
stores can stay configured alongside this source. If another source also
provides `apk`, check the selected entry before installing; source precedence
depends on the ZimaOS module manager version.

## 4. Install and use a package

Install the Alpine environment:

```sh
sudo zpkg install apk
sudo zpkg list
sudo apk --version
sudo apk environment
```

`zpkg list` reports installed modules. `apk environment` shows the Alpine
environment and its repository configuration. This package provides command
line tools and has no dashboard tile. See [Using apk](../packages/apk/README.md)
for installing Alpine packages, running their commands, editing Alpine settings,
and backing up persistent data.

There are two different package catalogs: `zpkg update` refreshes ZimaOS
modules; `sudo apk update` refreshes Alpine packages inside the installed
environment.

## 5. Update packages and ZimaOS

Refresh the module catalog, inspect the available versions, and upgrade only
the module you want:

```sh
sudo zpkg update
sudo zpkg list-remote
sudo zpkg list
sudo zpkg upgrade apk
```

The `apk` module's persistent data survives extension upgrades. Updating Alpine
software inside that environment uses `sudo apk upgrade`; see the package guide
for its separate lifecycle.

Before upgrading ZimaOS, check whether this repository publishes images for the
new exact `VERSION_ID`. Back up your package data. After the OS upgrade, change
only this repository's `modstore` line to the matching version-specific URL,
restart `zimaos.service`, and refresh the list. If an existing extension cannot
activate, install the compatible image through `zpkg`. A rebuilt image can have
the same module version while targeting a new OS version; in that case removing
and reinstalling the module selects the new image. The `apk` package retains
its persistent data during removal, but check each package's guide before doing
this with other modules.

## 6. Remove a package or repository

To remove the `apk` module:

```sh
sudo zpkg remove apk
```

Its persistent Alpine data remains on `/DATA`; its managed command launchers
are removed when its service stops. Reinstalling a compatible `apk` image reuses
that data. Removing an Alpine package with `sudo apk del PACKAGE_NAME` has a
different effect: it removes that package from the Alpine environment and keeps
the `apk` module installed.

To disconnect this repository, edit `/etc/casaos/mod-management.conf` and delete
only the line containing this project's URL. Keep all other sources and
settings. Then run:

```sh
sudo systemctl restart zimaos.service
sudo zpkg update --clean-upgradable-cache
```

Disconnecting a repository does not uninstall modules or delete their data. To
undo your initial configuration edit completely, you can restore the backup,
provided you have made no other settings changes since taking it:

```sh
sudo cp -p /etc/casaos/mod-management.conf.before-zimaos-packages /etc/casaos/mod-management.conf
sudo systemctl restart zimaos.service
sudo zpkg update --clean-upgradable-cache
```

## Optional: verify a downloaded bundle

The [repository information](https://runui.github.io/zimaos-packages/repository.json)
lists each image's OS, architecture, SHA-256, `bundle_url`, and `checksums_url`.
Download the bundle for your exact compatibility from its `bundle_url`.
Extract it into a new directory and check its files:

```sh
mkdir apk-download
tar -xzf apk.bundle.tar.gz -C apk-download
cd apk-download
sha256sum -c apk.SHA256SUMS
```

Every check must report `OK`. The bundle includes the raw image, module
metadata, build provenance, pinned upstream input, notices, and declared package
artifacts. Install the verified image on your compatible ZimaOS device with:

```sh
sudo zpkg install ./apk.raw
```

Checksums detect damaged or changed downloads; use the trusted HTTPS repository
URL as your source. Module entries use the same `name`, `version`, and `url`
fields as the official Mod-Store. Extra checksum and compatibility information
is provided in `repository.json`; checksum enforcement by the installed `zpkg`
client depends on its own implementation.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Repository returns HTTP 404 | Confirm the exact version-specific URL and that a successful deployment exists. A version that has not been published has no endpoint. |
| `apk` is absent from `zpkg list-remote` | Check that the line is inside `[server]`, keep existing sources, restart `zimaos.service`, and run `sudo zpkg update` again. |
| `zpkg` cannot connect just after restarting | Wait a few seconds, then check `sudo systemctl status zimaos.service` and retry. |
| Download times out or HTTPS fails | Check the device's Internet connection, DNS, date, and access to `runui.github.io`. Test the catalog with `curl -fL` followed by its URL. |
| Image is rejected or fails to activate | Compare `ID`, exact `VERSION_ID`, and `uname -m` against `repository.json`. Use a compatible image. |
| Installed version differs from the expected version | Run `sudo zpkg update --clean-upgradable-cache` and compare `sudo zpkg list` with `sudo zpkg list-remote`. Check other configured sources for the same module name. |
| `apk` requires root or a command is not found | Use `sudo apk ...`. For Alpine tools, use `sudo apk exec COMMAND ...`; host commands with the same name take precedence. |
| Alpine downloads fail after the module is installed | Check `sudo apk environment` and the Alpine repository settings described in the package guide. |

When reporting a problem, include your ZimaOS version, architecture, repository
URL, package name, and the error message. Remove passwords, tokens, and other
private information from anything you share.
