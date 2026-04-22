# homebase Final Architecture

## Goal

`homebase` is designed as a separated home infrastructure system with distinct planes for:

- user interaction
- infrastructure hosting
- control and observability

The architecture is centered on three top-level nodes:

- `control`
- `workstation`
- `host`

The `host` node provides virtualized infrastructure through Proxmox and contains the following subnodes:

- `host.storage`
- `host.app`
- `host.llm-agents`

## Architectural Principles

- separate interactive use from always-on infrastructure
- separate storage from application hosting
- separate control-plane operations from workload execution
- keep services internal by default
- use Tailscale for remote access into the private environment
- centralize operational control through `control`
- make the future `homebase` CLI the main operator interface
- prefer private ingress by default while still allowing explicit temporary public exposure for selected services

## Top-Level Architecture

### `control`

`control` is the management plane for the entire system.

It is intended to be:

- always on
- lightweight
- reachable
- trusted as the operator entry point

Primary functions:

- orchestration
- health checks
- controlled command execution
- SSH control path
- Wake-on-LAN and reboot actions
- future `homebase` CLI hosting
- ingress gateway for selected internal web services
- ingress mode switching for services that need to move between private-only and temporary public access

Design constraints:

- stay lightweight enough for Raspberry Pi 4 hardware
- avoid becoming the primary storage location for time-series or log data
- remain operational even if `host.app` is unavailable

Current implementation target:

- Raspberry Pi 4 Model B with 4 GB RAM
- Debian 13 on a 64 GB-class SD card device

### `workstation`

`workstation` is the main user machine.

It is intentionally separated from the always-on server environment and remains the place for:

- daily productivity
- development
- browsing
- gaming
- Sunshine hosting for Moonlight clients

### `host`

`host` is the main infrastructure server.

It is responsible for:

- running Proxmox
- hosting virtual machines
- providing persistent storage infrastructure
- running self-hosted services
- hosting isolated agent workloads

## Virtualization Layout

### `host.storage`

Purpose:

- primary data storage
- NAS and cloud data backend
- snapshot source
- backup source

Planned stack:

- TrueNAS
- ZFS

### `host.app`

Purpose:

- self-hosted apps
- Docker workloads
- media services such as Plex
- personal web services
- Prometheus
- Grafana
- persistent observability backends

### `host.llm-agents`

Purpose:

- isolated execution environment for LLM agent workflows
- autonomous task execution within a VM boundary

Security expectation:

- stronger isolation than ordinary service workloads
- treated as a higher-risk environment

## Operational Model

The target operational model is:

- `control` acts as the primary management entry point
- `host` runs the core infrastructure workloads
- `host.app` hosts observability backends and other persistent service workloads
- `workstation` remains the user-facing machine
- predefined commands are preferred over ad hoc manual operations
- service ingress should default to private paths and only move to broader exposure through explicit operator action

Failure model:

- `control` must remain usable for CLI, SSH, and recovery-oriented operations when `host.app` is down
- monitoring-backed CLI features may degrade gracefully when Prometheus or Grafana are unavailable

Ingress model:

- dedicated service subdomains should be preferred over path-based routing
- normal remote access should flow through private ingress on `control`
- selected services may later support temporary public exposure when explicitly enabled by the operator
- temporary public exposure should be easy to revert and should not become the default steady state

This architecture is intended to support the future `homebase` CLI, where the system can be explored and operated through hierarchical resource paths.

## Resource Hierarchy

Canonical CLI hierarchy:

- `control`
- `workstation`
- `host`
- `host.storage`
- `host.app`
- `host.llm-agents`

Current VM names on the Proxmox host:

- `storage`
- `app`
- `llm-agents`

## Architecture Summary

`homebase` uses a three-node top-level design:

- one control node
- one user workstation
- one infrastructure host

The infrastructure host is subdivided into three VMs:

- one storage VM
- one app VM
- one isolated agent VM

This gives the system clear separation between management, interactive computing, persistent storage, application hosting, and higher-risk autonomous workloads.

The `homebase` repository itself should live on NAS-backed storage so the operational workspace and its artifacts remain centralized with the rest of the infrastructure state.
