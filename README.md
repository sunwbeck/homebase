# homebase

`homebase` is a controller CLI and lightweight node client for managing a personal home-server setup.

The installed command is `homebase`. `hb` is just the short alias.

What it does:

- discovers and registers managed nodes
- shows node information and basic status
- runs a small background service on each node
- installs and updates `homebase` from GitHub refs
- lets the controller node request installs and updates on other paired nodes

## Install

First install:

```bash
curl -fsSL https://raw.githubusercontent.com/sunwbeck/homebase/main/scripts/install-homebase.sh | bash
```

Install a specific ref:

```bash
curl -fsSL https://raw.githubusercontent.com/sunwbeck/homebase/main/scripts/install-homebase.sh | bash -s -- --ref main
```

The installer sets up `homebase` for the current user and makes `homebase` and `hb` available from `~/.local/bin`.

On first run, `homebase` or `hb` starts `homebase init` automatically if the local node role or node name has not been set yet.

## Basic Flow

On the controller node:

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
homebase connect code --refresh
homebase service start
```

Pairing flow:

1. start `homebase service start` on the target node
2. read the 8-digit code from `homebase connect code --refresh`
3. run `homebase connect scan` on the controller node
4. run `homebase connect add` on the controller node and enter the code

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

Use `homebase status` when you want one live overview of all registered nodes.

```bash
homebase status
```

Use object commands when you want to inspect or change one area directly.

```bash
homebase node list
homebase node show host.app
homebase node edit host.app name host.api
homebase node edit host.app role managed
homebase node assign host.app app-tier
homebase node unassign host.app app-tier
homebase group list
homebase group show app-tier
homebase group add app-tier
homebase group edit app-tier name app-services
homebase group edit app-services description "app workloads"
homebase group remove app-tier
homebase link list
homebase link add host-node app-tier
homebase link remove host-node app-tier
```

Use `homebase inventory` when you want to inspect or edit the ansible-style YAML.

```bash
homebase inventory
homebase inventory show
homebase inventory edit
```

`homebase inventory` shows inventory help. `homebase inventory show` refreshes the ansible YAML and prints it. `homebase inventory edit` opens that YAML in your editor.

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
