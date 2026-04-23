# homebase-cli

This directory contains the Python package for `homebase`.

The installed command is `homebase`. `hb` is just the short alias.

It provides two things in one package:

- the operator CLI used on the control node
- the small client service used on managed nodes

## Main Commands

General:

```bash
homebase init
homebase role
homebase roles
homebase ls
homebase info host.app
```

Client side:

```bash
homebase client code --refresh
homebase client serve
```

Control side:

```bash
homebase node scan
homebase node add
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
