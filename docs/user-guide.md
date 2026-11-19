# User configuration guide

This guide explains how to use the published ZimaOS package repository. You need
a ZimaOS device, an administrator account with SSH access, and an Internet
connection. ZimaOS already provides `zpkg`; you do not need to install a package
manager or compile this project.

This is a community repository. Keep your existing module sources and choose
the source that matches your device's architecture.

## 1. Connect and check your device

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

Confirm that `ID=zimaos` in the first command's output, then choose the target
matching `uname -m`. You do not need a repository URL for a particular ZimaOS
version.

| OS ID | Architecture from `uname -m` | Repository directory |
| --- | --- | --- |
| `zimaos` | `x86_64` | `zimaos/amd64` |
| `zimaos` | `aarch64` | `zimaos/arm64` |

For x86_64, use:

```text
https://runui.github.io/zimaos-packages/zimaos/amd64/mod-v2.json
```

For ARM64 (`aarch64`), use:

```text
https://runui.github.io/zimaos-packages/zimaos/arm64/mod-v2.json
```

Use the [published repository information](https://runui.github.io/zimaos-packages/repository.json)
to check available targets and packages. The above endpoints appear after
the first successful deployment. Package requirements, including host features
and tools, are described in each package's guide. Renaming an image does not
change its architecture or runtime requirements.

Use the architecture-specific URL above. It stays the same across ZimaOS
upgrades.

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
line inside it. These examples use x86_64; for ARM64, replace `zimaos/amd64`
with `zimaos/arm64` in the URL:

```ini
modstore = https://runui.github.io/zimaos-packages/zimaos/amd64/mod-v2.json
```

Keep every existing setting and `modstore` line. Multiple repositories use
repeated `modstore` keys. Add only the entry for your device's architecture:
both stores provide the same module names, with different executable images.
If there is no `[server]` section, add one at the end
of the file, followed by the new line:

```ini
[server]
modstore = https://runui.github.io/zimaos-packages/zimaos/amd64/mod-v2.json
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
should contain `apk`, `oci-mirror-proxy`, and their published module versions.
Refreshing the list does not upgrade installed packages.

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

To install the Docker registry mirror proxy:

```sh
sudo zpkg install oci-mirror-proxy
oci-mirror-proxy status
```

Its service initializes configuration, downloads the upstream mirror catalog,
and automatically connects local Docker. See [Using the proxy](../packages/oci-mirror-proxy/README.md)
for subscription settings, updates, persistence, and Docker cleanup on removal.

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

Back up your package data before upgrading ZimaOS. Keep the same repository URL
after the upgrade, then run `sudo zpkg update` to check for package updates.
The published packages do not impose an exact host OS version merely because
ZimaOS has a new version number. They still need the kernel features, host tools,
and APIs described in their own guides. If a package fails after an OS upgrade,
check its requirements and available updates before reinstalling it.

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
Download the bundle for your device's target from its `bundle_url`.
Large files use GitHub's Git LFS content URLs on `media.githubusercontent.com`;
each image or bundle is still one complete file. The module store and download
directory link to the appropriate location automatically. No Git or Git LFS
client is needed to download or install packages.
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
| Repository returns HTTP 404 | Use the `zimaos/amd64/` or `zimaos/arm64/` URL above and confirm a successful deployment exists. |
| `apk` is absent from `zpkg list-remote` | Check that the line is inside `[server]`, keep existing sources, restart `zimaos.service`, and run `sudo zpkg update` again. |
| `zpkg` cannot connect just after restarting | Wait a few seconds, then check `sudo systemctl status zimaos.service` and retry. |
| Download times out or HTTPS fails | Check the device's Internet connection, DNS, date, and access to `runui.github.io`. Test the catalog with `curl -fL` followed by its URL. |
| Image is rejected or fails to activate | Check the target OS and architecture in `repository.json`, the package's runtime requirements, and the reported error. A ZimaOS upgrade does not require a different repository path. |
| Installed version differs from the expected version | Run `sudo zpkg update --clean-upgradable-cache` and compare `sudo zpkg list` with `sudo zpkg list-remote`. Check other configured sources for the same module name. |
| `apk` requires root or a command is not found | Use `sudo apk ...`. For Alpine tools, use `sudo apk exec COMMAND ...`; host commands with the same name take precedence. |
| Alpine downloads fail after the module is installed | Check `sudo apk environment` and the Alpine repository settings described in the package guide. |

When reporting a problem, include your ZimaOS version, architecture, repository
URL, package name, and the error message. Remove passwords, tokens, and other
private information from anything you share.
