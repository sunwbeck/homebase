# homebase Docs Index

## Purpose

This document is a short map of where information lives in the `homebase` documentation set.

## Core Documents

`README.md`

- top-level entry point
- short description of the project
- links to the main documents

`docs/current-state.md`

- current implementation status
- which VMs already exist
- immediate next steps
- current implementation direction and active working policy

`docs/system-overview.md`

- overall system structure
- top-level nodes and their roles
- current VM naming
- CLI resource naming direction
- high-level repository location policy

`docs/final-architecture.md`

- target end-state architecture
- separation between storage, app, and agent planes
- operational model and failure assumptions

`docs/node-roles.md`

- role boundaries for each node
- what each subnode is responsible for
- what each subnode should avoid owning

`docs/network-and-access-policy.md`

- physical network layout
- access rules
- SSH policy direction
- Tailscale-based remote access model

`docs/control-plane.md`

- responsibilities of the `control` node
- future CLI direction
- observability placement
- what should still work if the app VM is down
- tailnet ingress and reverse proxy direction through `control`

`docs/storage-and-backup.md`

- storage node direction
- disk layout options
- backup approach
- repository and docs workflow

## CLI Documents

`docs/homebase-cli-development-plan.md`

- CLI product goals
- canonical resource model
- suggested module layout
- delivery phases
- operational assumptions for CLI behavior

`docs/homebase-cli-checklist.md`

- execution checklist for CLI implementation
- handoff rules across work sessions
- current progress markers

## Migration Document

`docs/nas-migration.md`

- historical NAS migration notes
- copy, verification, symlink, and rollback steps

## Suggested Reading Order

1. `README.md`
2. `docs/current-state.md`
3. `docs/system-overview.md`
4. `docs/node-roles.md`
5. `docs/storage-and-backup.md`
6. `docs/homebase-cli-development-plan.md`
