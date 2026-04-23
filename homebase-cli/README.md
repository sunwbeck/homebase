# homebase-cli

This directory contains the Python package for `homebase`.

The installed command is `homebase`. `hb` is just the short alias.

It provides three things in one package:

- the operator CLI used on the controller node
- the managed connect endpoint used for pairing and remote requests
- the local background daemon used on both controller and managed nodes

## Main Commands

General:

```bash
homebase init
homebase role show
homebase role list
homebase status
homebase inventory --help
homebase package status
homebase package versions
```

Managed node:

```bash
homebase connect code --refresh
homebase connect status
homebase daemon start
homebase daemon status
homebase service list
```

Controller node:

```bash
homebase status
homebase connect scan
homebase connect add
homebase group add app-tier
homebase role show host.app
homebase role edit host.app managed
homebase service list
homebase service list --group app-tier
homebase service show host.app
homebase node show host.app
homebase node assign host.app app-tier
homebase node edit host.app
homebase link add app-tier host-group
homebase inventory
homebase inventory edit
homebase package status host.app
homebase package status --group app-tier
homebase package install host.app --ref v0.1.1
homebase package install --group app-tier --ref v0.1.1
homebase package update host.app
homebase package update --all
```

Local package management:

```bash
homebase package versions
homebase package status
homebase package install --ref main
homebase package update
homebase package status --all
homebase package update --group app-tier
```

## Development

Project layout:

```text
homebase-cli/
  pyproject.toml
  README.md
  docs/
  scripts/
  src/homebase_cli/
  tests/
```

Run tests:

```bash
python -m pytest
```

If you need the current repo version installed locally again:

```bash
bash ../scripts/install-homebase.sh
```

The goal for the CLI help is self-guided operation. `homebase --help` and each subcommand help screen should be enough for an operator or an LLM to find the next command without external notes.

For the longer manual and repo docs:

```bash
homebase doc
homebase doc current-state
homebase doc cli-plan
```
