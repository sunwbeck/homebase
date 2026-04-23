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
homebase connect scan
homebase connect add
homebase group add host-node
homebase group add app-tier
homebase link add host-node app-tier
homebase node assign host.app app-tier
homebase state set host.app status active
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

`homebase init` registers the current machine with a local node name as well as a node type.

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

- `versions`: list installable GitHub refs such as releases, tags, or the default branch
- `status`: show what is installed on the current node
- `status <resource>`: ask a paired node what is installed there
- `install`: install a specific branch, tag, or commit
- `update`: move to the latest release, or the default branch if no release exists

## Inventory And State Commands

Use `homebase status` when you want one overview of everything currently registered.

```bash
homebase status
```

Use object commands when you want to inspect or change one area directly.

```bash
homebase role show
homebase role set control
homebase node list
homebase node show host.app
homebase node rename host.app host.api
homebase node set-role host.app managed
homebase node assign host.app app-tier
homebase node unassign host.app app-tier
homebase group list
homebase group show app-tier
homebase group add app-tier
homebase group rename app-tier app-services
homebase group remove app-tier
homebase link list
homebase link add host-node app-tier
homebase link remove host-node app-tier
```

Use `homebase inventory` when you want to inspect or edit the ansible-style YAML.

```bash
homebase inventory
homebase inventory --open
```

`homebase inventory` refreshes the ansible YAML and prints the YAML contents. `homebase inventory --open` opens that YAML in your editor.

`homebase state` stores simple key/value labels on registered nodes.

```bash
homebase state set host.app status active
homebase state set host.app site home
homebase state show host.app
homebase state unset host.app site
```

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
