# homebase-cli

This directory contains the Python package for `homebase`.

It provides two things in one package:

- the operator CLI used on the control node
- the small client service used on managed nodes

## Main Commands

General:

```bash
hb init
hb role
hb roles
hb ls
hb info host.app
```

Client side:

```bash
hb client code --refresh
hb client serve
```

Control side:

```bash
hb node scan
hb node add
hb package status host.app
hb package install host.app --ref v0.1.1
hb package upgrade host.app
```

Local package management:

```bash
hb package versions
hb package status
hb package install --ref main
hb package upgrade
hb package update
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
