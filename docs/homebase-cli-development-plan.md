# homebase CLI Development Plan

## Purpose

This document defines how the `homebase` CLI should be designed and delivered so implementation can continue cleanly across different sessions, contributors, or tools without losing context.

The CLI is intended to become the primary operator interface for the `homebase` system.

## Product Goal

The `homebase` CLI should provide:

- hierarchical resource discovery
- completion-friendly navigation
- structured status inspection
- predefined remote operations
- a stable operator UX across `control`, `workstation`, `host`, and `host` subnodes
- a first-class way to inspect and change service exposure policy
- a safe way to switch one service between normal private ingress and temporary public ingress

The CLI should make the system structure visible to the operator while reducing ad hoc direct SSH usage.

It should also provide a workable path for privilege-requiring recovery tasks without assuming that all required `sudo` commands can be known in advance.

## Current Workflow Direction

The current implementation direction should favor an agent-first workflow over generic LAN guessing.

The preferred architecture is control-centric with a thin client on each managed node.

Design rule:

- `control` remains the operator entry point
- each managed node runs a lightweight `homebase` client
- the client should self-report identity and current local facts
- the client should not become a heavy autonomous management plane
- long-lived metrics storage still belongs on `host.app`, not on each client
- `control` should keep the registry-owned role-group definitions and node role assignments

Near-term operator flow:

1. install `homebase` on the target node
2. run `homebase connect code --refresh` on the target node and read the 8-digit code locally
3. run `homebase daemon start` on the target node
4. run `homebase connect scan` from `control` to discover responding homebase clients
5. run `homebase connect add`, choose one discovered device, and enter the 8-digit code shown on the target node

This keeps discovery generic while making node identity explicit and self-reported. It also separates discovery from trust:

- pre-pairing scan data should stay minimal
- full ports and service inventory should only be fetched after successful pairing

## Thin Client Responsibilities

The node client should stay intentionally small.

Required responsibilities:

- expose minimal discovery identity before pairing
- generate and validate a local pairing code
- expose a full node profile only after successful pairing
- report current address and last-seen information to `control`
- collect lightweight local facts such as hostname, platform, open ports, and running services
- receive synchronized node context files managed by `homebase`
- later execute predefined operations requested by `control`

Non-goals for the client:

- do not store long-lived metrics or logs
- do not become the policy owner
- do not perform complex orchestration on its own
- do not own Prometheus or Grafana-like storage-heavy services

## Canonical Resource Model

The CLI should treat these as canonical resource paths:

- `control`
- `workstation`
- `host`
- `host.storage`
- `host.app`
- `host.llm-agents`

These paths are CLI-facing identifiers, not necessarily runtime hostnames.

## Initial Scope

Phase 1 should focus on read-only and low-risk operations first.

### Must-have initial commands

- `homebase status`
- `homebase status <resource>`
- `homebase info <resource>`
- `homebase ls`
- `homebase ls <resource>`
- `homebase connect code`
- `homebase connect scan`
- `homebase connect add`
- `homebase daemon start`
- `homebase ssh <resource>`

### Next commands

- `homebase restart <resource>`
- `homebase wake <resource>`
- `homebase shutdown <resource>`
- `homebase metrics <resource>`
- `homebase check`
- `homebase network status`
- `homebase network status <resource>`
- `homebase network explain <resource> <service>`
- `homebase network expose <resource> <service> private`
- `homebase network expose <resource> <service> public --ttl <duration>`

### Later commands

- `homebase service list <resource>`
- `homebase service restart <resource> <service>`
- `homebase network group create <name>`
- `homebase network group rule add <name> ...`
- `homebase network bind <resource> <service> <group>`
- `homebase network reconcile`
- `homebase network expose <resource> <service> <mode>`
- `homebase backup status`
- `homebase snapshot status`
- `homebase doctor`

## UX Requirements

The CLI should feel informative rather than minimal.

Required UX characteristics:

- hierarchical resource completion
- discoverable targets
- stable command grammar
- readable terminal output
- consistent error messages
- explicit mapping from resource path to action target

Examples:

```text
homebase status
homebase status host
homebase status host.app
homebase ls host
homebase ssh host.storage
```

## Recommended Implementation Approach

### Language

Use Python for the initial implementation.

Reason:

- strong CLI library support
- good SSH, config, and structured output support
- easy local iteration on `control`

### Installation and rollout direction

Use a normal Python project layout with an installable CLI entry point.

Suggested shape:

```text
homebase/
  src/homebase_cli/
  tests/
  pyproject.toml
```

The same package should provide both:

- the operator CLI used on `control`
- the lightweight client commands used on managed nodes

Version and rollout preference:

- GitHub should own `homebase` history, tags, releases, and rollout targets
- `homebase` should not grow its own parallel package registry or version catalog
- the control plane should decide which GitHub ref is the desired target for one node or many nodes

Near-term install preference:

- installation should follow normal Python expectations
- if the operator activates a venv first, install should go into that venv
- if the operator runs install outside a venv, install should go into the current Python environment
- `homebase` should avoid project-specific install target concepts where normal Python behavior is already clear

Long-term rollout preference:

- `control` should track which `homebase` revision each managed node reports
- `control` should support canary rollout to one node first and broader rollout later
- node operators should not have to SSH into each node to choose a version manually
- GitHub branch, tag, release, or commit selection should be expressed at the control-plane level

### Suggested internal modules

- `cli.py`: command entry point
- `resources.py`: resource tree and lookup logic
- `inventory.py`: mapping from CLI resource path to runtime host data
- `runner.py`: remote execution helpers
- `status.py`: status collectors and formatters
- `metrics.py`: Prometheus-related integrations or queries against the backend on `host.app`
- `config.py`: config loading and validation
- `output.py`: terminal rendering helpers
- `network_groups.py`: user-defined exposure group model and validation
- `service_map.py`: service-to-port ownership mapping
- `reconcile.py`: realization of declared network policy into proxy and firewall state
- `ingress.py`: mapping from exposure mode to proxy, DNS, and firewall realization
- `packaging.py`: install and rollout helpers, later GitHub-ref-aware deployment helpers
- `context.py`: node context file sync and rendering helpers

## Source of Truth Rules

The implementation should maintain one canonical definition of resource identity.

Required rule:

- define resource hierarchy once
- derive completion, lookup, and display behavior from that hierarchy

This avoids duplicated target lists across:

- shell completion
- command validation
- output rendering
- SSH target mapping

## Configuration Model

The CLI should not hardcode environment-specific addresses directly in command handlers.

Recommended configuration layers:

- static resource hierarchy in code
- runtime mapping in config file
- secrets and keys handled outside the repository where appropriate

Suggested config content:

- runtime hostname
- reachable address or operator-defined endpoint if needed
- SSH user
- SSH method
- WOL MAC address where applicable
- node capabilities
- service definitions and owned ports
- user-defined exposure groups
- service-to-group bindings
- ingress mode definitions and mode-specific endpoints
- temporary public exposure settings such as TTL or auth requirement

## Operational Model

The CLI should prefer predefined operations over unrestricted arbitrary execution.

Control-plane assumptions:

- the CLI runs on `control`
- Ansible runs from `control`
- Prometheus and Grafana run on `host.app`
- the CLI must remain useful for status, SSH, and recovery operations even when `host.app` is unavailable
- `control` is the first planned private ingress node for service subdomains

Privilege-related assumptions:

- routine operations should prefer predefined non-destructive commands
- some real incident response tasks will still require arbitrary privileged commands over SSH
- the operator may need those actions while away from home
- the CLI should therefore support an explicit exception-handling elevation workflow rather than pretending all needed `sudo` operations can be pre-whitelisted

Priority order:

1. status and inspection
2. controlled access
3. power and service operations
4. monitoring integration
5. advanced automation

## Development And Verification Workflow

Development should distinguish between three validation loops.

### 1. Local fast loop

Use this on the development machine before touching another node.

- run unit tests
- run CLI command tests
- run a local client self-test against an ephemeral loopback server
- verify pairing, discovery, and profile fetch without needing another machine

This loop should catch:

- command wiring regressions
- payload format regressions
- pairing logic regressions
- install helper regressions

### 2. Canary node loop

Use one non-critical node as the repeated remote smoke-test target.

Recommended order:

1. commit and push candidate changes
2. choose one GitHub ref as the canary target
3. have `control` install or upgrade that canary node to the chosen ref
4. restart or validate the client service if needed
5. run `homebase connect scan` and a pairing or status smoke test from `control`

The canary node should be the first place where:

- install and upgrade friction is measured
- persistent client service behavior is verified
- real network discovery behavior is checked

### 3. Broader rollout loop

Only after the canary node succeeds:

- roll the same GitHub ref onto additional nodes from `control`
- verify discovery and pairing consistency
- verify service restart and recovery behavior

## Installation Strategy During Development

Repeated install and upgrade must be cheap, because the client needs to be tested on real nodes.

Preferred development-time model:

- keep the source repo on the main development machine
- use GitHub as the operator-facing identity for what should be installed
- let `control` orchestrate install or upgrade of that chosen ref onto managed nodes
- treat reinstall and upgrade as normal and expected during development

Recommended near-term commands should evolve toward:

```text
git push
homebase package install <resource> --ref <git-ref>
homebase package update <resource>
homebase daemon start
```

The important rule is that `git` and GitHub define the revision, while `homebase` performs the install and orchestration work.

## Persistent Client Runtime

The client should not rely on a manually opened shell for routine use.

Near-term recommendation:

- run the node client as a systemd service on Linux nodes
- keep the service simple: one process, one listen port, one state file location
- make reinstall plus service restart the standard update path

This gives a practical update loop:

1. choose a GitHub rollout target
2. install that target on one node
3. restart `homebase-client` if needed
4. validate from `control`

## Context Distribution Direction

Node-local context for LLM or automation work should be managed by `homebase` rather than copied manually.

Recommended shape:

- one canonical context source under `control`
- synchronized rendered files on each node
- layered context files such as `base`, `role`, `local`, and generated facts

The client should later expose a narrow file sync surface for these managed context files.

Network exposure inspection belongs early in this order because it is part of structured status, not merely an advanced later automation feature.

This keeps the early CLI useful while limiting unnecessary operational risk.

## Privilege Elevation UX Direction

The CLI should distinguish between:

- normal predefined operations
- exception-mode recovery operations

Normal mode should continue to favor:

- status inspection
- structured service and network visibility
- predefined restart, wake, shutdown, and recovery commands

Exception mode should support cases where:

- a node must be reached over SSH
- a privileged command is required
- the exact command was not predictable before the incident

Design goals for exception mode:

- require explicit operator intent before elevation
- prefer time-limited elevation windows
- avoid storing or transmitting the raw `sudo` password through chat text
- support later auditability of when elevation was requested and used
- keep routine UX simple while still allowing break-glass recovery when needed

Candidate command direction:

```text
homebase ssh host.app
homebase elevate host.app --ttl 5m
homebase recover host.app
homebase recover host.app --interactive
```

These are only direction markers.

The final implementation may use:

- a helper that opens a temporary privileged session after local approval
- a node-local or control-local approval service
- a secret-manager-backed approval flow
- a documented manual operator step that the CLI can detect and then use safely

The important product rule is that the CLI must acknowledge emergency privilege elevation as a first-class operational need rather than treating it as an out-of-band hack.

## Delivery Phases

### Phase 0: project bootstrap

Deliverables:

- Python project skeleton
- CLI entry point
- base documentation
- basic command parsing

Definition of done:

- `homebase --help` works
- `homebase ls` returns known top-level resources
- resource model exists in code

### Phase 1: resource tree and completion

Deliverables:

- canonical resource tree
- lookup and validation layer
- shell completion support

Definition of done:

- `control`, `workstation`, `host`, and host subresources are represented in code
- invalid resource paths fail cleanly
- shell completion can suggest valid next targets

### Phase 2: status and info

Deliverables:

- `status` command
- `info` command
- human-readable output format
- initial service and network exposure inventory model
- initial service subdomain inventory model

Definition of done:

- operator can inspect top-level nodes
- operator can inspect `host.storage`, `host.app`, and `host.llm-agents`
- output clearly indicates success, failure, or unknown state
- operator can see at least a placeholder or static mapping from service name to owned ports
- operator can see at least a placeholder mapping from service name to intended subdomain

### Phase 3: SSH and command routing

Deliverables:

- `ssh <resource>`
- target-to-runtime mapping
- structured remote command runner

Definition of done:

- CLI can route to the correct target
- mapping is centralized
- SSH behavior is documented

### Phase 3.5: network exposure model and grouped status

Deliverables:

- user-defined exposure group schema
- service binding schema
- `network status`
- `network explain`
- support for distinguishing private ingress from temporary public ingress

Definition of done:

- operator can inspect service exposure grouped by operator-defined group names
- output distinguishes declared exposure from realized state
- a service can be traced from node, to service name, to port, to current exposure binding
- output can show whether a service is currently private-only or temporarily public

### Phase 4: operational actions

Deliverables:

- restart, wake, shutdown workflows
- guardrails for risky actions
- `network group create`
- `network group rule add`
- `network bind`
- `network reconcile`
- `network expose <resource> <service> private`
- `network expose <resource> <service> public --ttl <duration>`

Definition of done:

- actions are explicit
- confirmations or safe defaults exist where needed
- failure output explains what happened
- operator can change a service binding without hand-editing firewall rules
- temporary public exposure can be enabled and reverted through one command path

### Phase 5: monitoring integration

Deliverables:

- metrics-aware commands
- Prometheus integration path
- Grafana jump links or references if useful

Definition of done:

- operator can inspect basic monitoring-backed state through the CLI
- command output remains understandable even when monitoring data is unavailable
- CLI behavior is explicitly degraded but still useful when `host.app` is down

## Testing Strategy

The CLI needs both local correctness tests and system-aware integration validation.

### Unit test targets

- resource parsing
- target validation
- config loading
- command routing
- output formatting

### Integration test targets

- SSH path resolution
- status collection against reachable nodes
- behavior when a node is offline
- behavior when credentials are missing

## Non-Goals for Early Versions

- full arbitrary remote shell replacement
- complete provisioning automation on day one
- advanced dashboard generation inside the CLI itself
- broad write operations without guardrails

## Work Tracking Rules

Every meaningful implementation step should leave behind enough information for the next person or session to continue immediately.

Required practice:

- update this document if scope changes
- update the checklist document when tasks start or finish
- record assumptions in the relevant document instead of only in chat
- keep the canonical resource model synchronized with the docs

## Open Design Decisions

- exact Python CLI framework
- config file format
- whether status collection should pull from SSH, Prometheus, or both by default
- storage format for user-defined exposure groups and service bindings
- exact implementation of temporary public exposure, TTL, and rollback semantics
