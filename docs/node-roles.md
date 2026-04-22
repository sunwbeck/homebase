# homebase Node Roles

## Scope

This document defines the role and responsibility boundary for each node in `homebase`.

## Top-Level Nodes

### `control`

Role:

- control plane

Responsibilities:

- act as the main operator entry point
- host the future `homebase` CLI
- run orchestration tooling such as Ansible
- run lightweight monitoring and health-check logic
- collect or surface system status for the rest of `homebase`
- initiate SSH-based administrative operations
- initiate Wake-on-LAN, restart, and shutdown workflows

Boundaries:

- should stay lightweight and reliable
- should avoid unnecessary application workloads
- should avoid being the primary host for persistent metrics or log storage
- should not become the primary general-purpose service host unless explicitly justified

### `workstation`

Role:

- primary personal computing node

Responsibilities:

- documents
- coding
- browsing
- gaming
- Sunshine host for Moonlight clients

Boundaries:

- not the primary place for always-on infrastructure workloads
- should be manageable through the `homebase` control model where practical
- streaming-related exposure is an intentional exception to the otherwise centralized access pattern

### `host`

Role:

- infrastructure host

Responsibilities:

- run Proxmox
- host `homebase` virtual machines
- provide stable always-on compute and storage capacity

Boundaries:

- infrastructure functions should be delegated to subnodes rather than collapsed into the Proxmox host where possible
- direct workload sprawl on the Proxmox host should be avoided

## `host` Subnodes

### `host.storage`

Role:

- storage plane

Responsibilities:

- TrueNAS deployment
- ZFS storage management
- snapshots
- backup source management
- provide storage services to the rest of the system

Boundaries:

- should prioritize data integrity and operational simplicity
- should avoid taking on unrelated application responsibilities unless clearly required

Current VM name:

- `storage`

### `host.app`

Role:

- application service plane

Responsibilities:

- Docker runtime
- personal web apps
- media services
- self-hosted utilities
- Prometheus
- Grafana
- persistent observability and logging backends

Boundaries:

- should not absorb storage-plane responsibilities
- should remain separable from the storage VM for maintenance and fault isolation
- its failure should not remove basic control-plane access from `control`

Current VM name:

- `app`

### `host.llm-agents`

Role:

- isolated agent execution plane

Responsibilities:

- run LLM agent frameworks
- allow autonomous tasks within a constrained VM boundary

Boundaries:

- should be treated as higher-risk than the other VMs
- should be more tightly isolated in both permissions and network posture
- should not hold broader trust than is required for its tasks

Current VM name:

- `llm-agents`
