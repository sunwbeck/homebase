# homebase-cli Stack and Structure

## Goal

Build a local CLI that reads the NAS-backed `homebase` documentation and later grows into the operator entry point for the environment.

The CLI should stay simple at the start:

- inspect known resources
- show structured information
- route to the right host or VM later

## Recommended Stack

### Python

Use Python 3.11+.

Reason:

- fast enough for this CLI
- strong standard library for filesystem, subprocess, SSH-adjacent work, and config parsing
- easy to run on the Raspberry Pi control node later

### Typer

Use Typer for the CLI layer.

Reason:

- good command structure for `status`, `ls`, `info`, and `ssh`
- strong help output by default
- low friction for subcommands and typed arguments

### Rich

Use Rich for output formatting.

Reason:

- readable tables and status output
- good fit for hierarchical resource inspection
- keeps terminal UX clean without building a formatter from scratch

### pytest

Use pytest for tests.

Reason:

- standard and lightweight
- easy to cover parsing, config, and command routing

### TOML

Use TOML for local config.

Reason:

- Python 3.11 includes `tomllib`
- readable for small infrastructure mappings
- good fit for inventory-like static configuration

## Initial Architecture

Start with a small layered structure.

### CLI layer

Responsible for:

- command parsing
- help text
- argument validation at the command boundary

Files:

- `src/homebase_cli/cli.py`

### Resource model

Responsible for:

- canonical resource names
- hierarchy traversal
- lookup by resource path

Files:

- `src/homebase_cli/resources.py`

### Config and inventory

Responsible for:

- reading local config
- mapping CLI resource names to runtime targets
- exposing host metadata to commands

Files:

- `src/homebase_cli/config.py`
- `src/homebase_cli/inventory.py`
- `config/homebase.toml`

### Documentation reader

Responsible for:

- reading the NAS-backed `homebase` directory
- locating key docs
- exposing summaries or pointers for `info`-style commands

Files:

- `src/homebase_cli/docs_reader.py`
- `src/homebase_cli/paths.py`

### Installation and rollout helpers

Responsible for:

- installing `homebase` into the current Python environment on one node
- preparing first-install bootstrap behavior where needed
- later coordinating GitHub-ref-based rollout from `control`
- tracking which installed version each managed node reports back

Files:

- `src/homebase_cli/packaging.py`

### Local self-test

Responsible for:

- exercising discovery and pairing against a loopback client server
- providing a fast validation loop before testing on another node

Files:

- `src/homebase_cli/selftest.py`

### Output layer

Responsible for:

- consistent tables
- status lines
- error display

Files:

- `src/homebase_cli/output.py`

### Service and ingress model

Responsible for:

- service inventory
- exposure group bindings
- private versus temporary public ingress modes
- rendering provider-specific reverse proxy drafts from a provider-neutral model

Files:

- `src/homebase_cli/service_map.py`
- `src/homebase_cli/ingress.py`

## Proposed Package Layout

```text
src/homebase_cli/
  __init__.py
  cli.py
  paths.py
  resources.py
  config.py
  inventory.py
  docs_reader.py
  output.py
```

## First Commands

The first implementation batch should be:

- `homebase ls`
- `homebase ls <resource>`
- `homebase info <resource>`
- `homebase docs`

The next supporting commands should be:

- `homebase dev self-test`
- install or bootstrap helpers that work from a Git checkout or GitHub ref
- node lifecycle commands for install, upgrade, and version inspection

Why this order:

- no SSH dependency yet
- no host mutation yet
- proves the resource model and document integration first

## Suggested Resource Model

Canonical resource paths:

- `control`
- `workstation`
- `host`
- `host.storage`
- `host.app`
- `host.llm-agents`

Runtime target mapping can stay separate from this model.

## Decision Summary

- keep the implementation in Python
- use Typer and Rich rather than a custom parser
- keep NAS docs as input data, not as the CLI code location
- build resource discovery first, remote operations later
