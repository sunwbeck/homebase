# homebase

`homebase` is a control CLI and lightweight node client for managing a personal home-server setup.

The installed command is `homebase`. `hb` is just the short alias.

What it does:

- discovers and registers managed nodes
- shows node information and basic status
- runs a small client service on each managed node
- installs and updates `homebase` from GitHub refs
- lets the control node request installs and updates on other paired nodes

## Install

First install:

```bash
curl -fsSL https://raw.githubusercontent.com/sunwbeck/homebase/main/scripts/install-homebase.sh | bash
```

Install a specific ref:

```bash
curl -fsSL https://raw.githubusercontent.com/sunwbeck/homebase/main/scripts/install-homebase.sh | bash -s -- --ref main
```

The install script sets up `homebase` for the current user and makes `homebase` and `hb` available from `~/.local/bin`.

## Basic Flow

On the control node:

```bash
homebase init
homebase node scan
homebase node add
```

On a managed node:

```bash
homebase init
homebase client code --refresh
homebase client serve
```

Pairing flow:

1. start `homebase client serve` on the target node
2. read the 8-digit code from `homebase client code --refresh`
3. run `homebase node scan` on the control node
4. run `homebase node add` on the control node and enter the code

## Package Commands

`homebase package` is for checking installed revisions and installing or updating from GitHub.

```bash
homebase package versions
homebase package status
homebase package status host.app
homebase package install --ref v0.1.1
homebase package install host.app --ref v0.1.1
homebase package update
homebase package update host.app
```

What they do:

- `versions`: list available GitHub releases or tags with short notes
- `status`: show what is installed on the current node
- `status <resource>`: ask a paired node what is installed there
- `install`: install a specific branch, tag, or commit
- `update`: move to the latest release, or the default branch if no release exists

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
