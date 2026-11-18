# Using apk on ZimaOS

The `apk` module lets you install and run Alpine Linux software on ZimaOS.
Packages and settings remain available after reboot. Install it with `zpkg`
after following the
[repository configuration guide](../../docs/user-guide.md).

Use the image for your device's architecture and keep `/DATA` writable.

## Install Alpine software

```sh
sudo apk update
sudo apk add curl
sudo apk exec curl --version
sudo apk info
```

Use `sudo apk exec COMMAND ...` to run installed Alpine programs. This also
works when ZimaOS already has a command with the same name. Programs without
name conflicts are also available through `/DATA/.local/bin`; add that directory
to your shell's `PATH` to run them directly.

Open an Alpine shell when you need several commands:

```sh
sudo apk shell
```

Type `exit` to leave. Your data, home, and working directory are available to
installed programs. Commands run as root and can modify your ZimaOS files.

To update Alpine software or remove a package:

```sh
sudo apk upgrade
sudo apk del curl
```

The module uses Alpine **edge** repositories, `main` and `community`.
`apk upgrade` updates installed Alpine software. Update the module itself with
`sudo zpkg upgrade apk` and update ZimaOS through its normal update process.

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

Alpine settings persist. Change DNS through ZimaOS network settings.

## Use ZimaOS services

Installed programs can connect to services already running on ZimaOS, such as
Docker. Run the program with `sudo apk exec COMMAND ...` and follow its own
configuration guide. Installing a client does not start the service it uses.

## Keep or remove package data

Installed packages and Alpine settings are stored in `/DATA/.zimaos-packages/apk`.
They survive reboot and module upgrades.

`sudo zpkg remove apk` removes the module and keeps your packages and settings.
Reinstalling a compatible module reuses them. Removal also clears the command
shortcuts it created in `/DATA/.local/bin` and preserves other files there.
Do not delete the whole `/DATA/.local/bin` directory when cleaning up this module.

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
It includes installed packages and Alpine settings. Restore it with a compatible
`apk` module on a device with the same architecture as the original.

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
