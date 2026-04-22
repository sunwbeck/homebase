# homebase

`homebase` is a control CLI and lightweight node client for managing a personal home-server setup.

What it does:

- discovers and registers managed nodes
- shows node information and basic status
- runs a small client service on each managed node
- installs and upgrades `hb` from GitHub refs
- lets the control node request installs and upgrades on other paired nodes

## Install

First install:

```bash
curl -fsSL https://raw.githubusercontent.com/sunwbeck/homebase/main/scripts/install-homebase.sh | bash
```

Install a specific ref:

```bash
curl -fsSL https://raw.githubusercontent.com/sunwbeck/homebase/main/scripts/install-homebase.sh | bash -s -- --ref main
```

The install script uses the current venv if one is active. Otherwise it creates `~/.local/share/homebase-cli/.venv` and links `hb` and `homebase` into `~/.local/bin`.

## Basic Flow

On the control node:

```bash
hb init
hb node scan
hb node add
```

On a managed node:

```bash
hb init
hb client code --refresh
hb client serve
```

Pairing flow:

1. start `hb client serve` on the target node
2. read the 8-digit code from `hb client code --refresh`
3. run `hb node scan` on the control node
4. run `hb node add` on the control node and enter the code

## Package Commands

`hb package` is for checking installed revisions and installing or upgrading from GitHub.

```bash
hb package versions
hb package status
hb package status host.app
hb package install --ref v0.1.1
hb package install host.app --ref v0.1.1
hb package upgrade
hb package upgrade host.app
hb package update
```

What they do:

- `versions`: list available GitHub releases or tags with short notes
- `status`: show what is installed on the current node
- `status <resource>`: ask a paired node what is installed there
- `install`: install a specific branch, tag, or commit
- `upgrade` and `update`: move to the latest release, or the default branch if no release exists

## Repository Layout

- `docs/`: system and CLI planning documents
- `homebase-cli/`: Python package source, tests, and CLI code
- `scripts/install-homebase.sh`: install script used by the one-line bootstrap command

## Docs

- `docs/current-state.md`
- `docs/system-overview.md`
- `docs/control-plane.md`
- `docs/homebase-cli-development-plan.md`
- `docs/homebase-cli-checklist.md`
