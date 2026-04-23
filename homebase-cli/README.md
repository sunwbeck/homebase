# homebase-cli

This directory contains the Python package for `homebase`.

The installed command is `homebase`. `hb` is just the short alias.

It provides two things in one package:

- the operator CLI used on the controller node
- the small client service used on managed nodes

## Main Commands

General:

```bash
homebase init
homebase role show
homebase role list
homebase status
homebase inventory
homebase package status
homebase package versions
```

Client side:

```bash
homebase client code --refresh
homebase client serve
```

Control side:

```bash
homebase status
homebase connect scan
homebase connect add
homebase group add app-tier
homebase role show host.app
homebase role edit host.app managed
homebase node show host.app
homebase node assign host.app app-tier
homebase node edit host.app name host.api
homebase link add app-tier host-group
homebase inventory
homebase inventory edit
homebase state set host.app status active
homebase package status host.app
homebase package install host.app --ref v0.1.1
homebase package update host.app
```

Local package management:

```bash
homebase package versions
homebase package status
homebase package install --ref main
homebase package update
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
