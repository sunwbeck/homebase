# homebase CLI Checklist

## Purpose

This checklist exists so `homebase` CLI work can continue as if one person had been working on it continuously, even when implementation is split across sessions, contributors, or tools.

## Working Rules

- before starting work, read this checklist and `homebase-cli-development-plan.md`
- when starting a task, mark it as in progress
- when finishing a task, mark it as done and leave a short result note
- if scope changes, update the plan document in the same work session
- do not leave undocumented decisions only in terminal or chat history
- if a task is partially complete, record exactly what remains blocked

## Status Legend

- `[ ]` not started
- `[~]` in progress
- `[x]` completed

## Current Project State

- `[~]` CLI codebase has been bootstrapped
- `[~]` canonical resource model exists in code
- `[x]` agent-first node discovery exists through `homebase client serve` and `homebase node scan`
- `[x]` 8-digit client-generated pairing gate exists before full profile fetch
- `[~]` GitHub-based install and rollout workflow is partially implemented for the current node
- `[ ]` persistent client service packaging exists for managed nodes
- `[ ]` shell completion exists
- `[ ]` status command exists
- `[ ]` SSH command exists
- `[ ]` operational commands exist
- `[ ]` network exposure model exists
- `[ ]` grouped network status exists
- `[ ]` monitoring integration exists

## Execution Checklist

### 1. Bootstrap

- `[x]` create CLI source directory
- `[x]` create Python project metadata
- `[x]` define installable `homebase` entry point
- `[x]` add base README for the CLI package
- `[x]` choose and document the CLI framework
- `[x]` define GitHub-ref-based install workflow for one node
- `[ ]` define GitHub-ref-based rollout workflow from `control`
- `[x]` define simple first-install bootstrap path for fresh nodes

### 2. Resource model

- `[~]` implement canonical resources:
  - `control`
  - `workstation`
  - `host`
  - `host.storage`
  - `host.app`
  - `host.llm-agents`
- `[x]` add parent-child traversal logic
- `[~]` add resource validation
- `[x]` add resource metadata structure
- `[~]` keep code model aligned with the docs

### 3. Inventory and config

- `[x]` decide config format
- `[~]` define runtime mapping structure
- `[~]` support hostname/IP mapping per resource
- `[x]` support self-reported homebase client identity per resource
- `[x]` support pairing-gated full profile fetch per resource
- `[~]` support SSH user per resource
- `[ ]` support optional WOL MAC storage
- `[ ]` validate config load failures clearly
- `[ ]` define service inventory structure
- `[ ]` define service-to-port ownership structure
- `[ ]` define user-defined exposure group structure
- `[ ]` define service-to-group binding structure
- `[ ]` define service-to-subdomain mapping structure
- `[ ]` define ingress mode structure for private and temporary public exposure

### 4. Completion and discoverability

- `[ ]` implement shell completion
- `[ ]` ensure partial resource suggestions work
- `[ ]` ensure `host` reveals `host.storage`, `host.app`, `host.llm-agents`
- `[ ]` ensure command completion and resource completion coexist cleanly
- `[ ]` document shell activation steps

### 5. Read-only commands

- `[~]` implement `homebase ls`
- `[~]` implement `homebase info <resource>`
- `[x]` implement `homebase node scan`
- `[ ]` implement `homebase status`
- `[ ]` implement `homebase status <resource>`
- `[~]` define output format for unknown or offline nodes
- `[ ]` implement `homebase network status`
- `[ ]` implement `homebase network status <resource>`
- `[ ]` implement grouped service exposure output
- `[ ]` implement `homebase network explain <resource> <service>`
- `[ ]` show whether a service is private-only or temporarily public
- `[ ]` show active subdomain and public exposure TTL when applicable

### 6. Access commands

- `[ ]` implement `homebase ssh <resource>`
- `[ ]` centralize SSH target mapping
- `[ ]` document expected SSH prerequisites
- `[ ]` fail safely when SSH is unavailable

### 6.5. Network policy commands

- `[ ]` implement `homebase network group create <name>`
- `[ ]` implement `homebase network group rule add <name> ...`
- `[ ]` implement `homebase network bind <resource> <service> <group>`
- `[ ]` implement `homebase network reconcile`
- `[ ]` implement `homebase network expose <resource> <service> private`
- `[ ]` implement `homebase network expose <resource> <service> public --ttl <duration>`
- `[ ]` define safe behavior when declared state and realized state diverge
- `[ ]` define rollback behavior for temporary public exposure expiry

### 7. Operational commands

- `[ ]` implement `restart`
- `[ ]` implement `wake`
- `[ ]` implement `shutdown`
- `[ ]` add safety guardrails for impactful actions
- `[ ]` ensure command output states the target and the attempted action

### 8. Monitoring and health

- `[ ]` define initial health signals
- `[ ]` implement `homebase check`
- `[ ]` implement `homebase metrics <resource>`
- `[ ]` document Prometheus data dependencies from `host.app`
- `[ ]` define behavior when metrics backend is unavailable
- `[ ]` define which commands must still work when `host.app` is down

### 8.5. Client lifecycle and rollout

- `[~]` document how `homebase client serve` should run persistently on a node
- `[ ]` add a packaged systemd unit or rendered unit template for Linux nodes
- `[x]` add one install command that follows normal Python environment behavior
- `[x]` add one upgrade command driven by a GitHub ref or equivalent rollout target
- `[ ]` add a control-plane command to show installed `homebase` version or revision across nodes
- `[ ]` add a control-plane command to roll one GitHub ref to one or more nodes
- `[ ]` add one local self-test command for discovery and pairing
- `[ ]` define the canary rollout procedure before wider deployment
- `[ ]` define how non-Linux nodes such as `workstation` participate or opt out

### 9. Testing

- `[~]` add unit tests for resource parsing
- `[~]` add unit tests for config loading
- `[~]` add unit tests for command routing
- `[ ]` add unit tests for output formatting
- `[~]` define a minimal integration test procedure

### 10. Documentation and handoff

- `[~]` document project layout
- `[~]` document local development workflow
- `[~]` document deployment target for the CLI
- `[ ]` document service placement: `control` for CLI and Ansible, `host.app` for Prometheus and Grafana
- `[ ]` document how to add a new resource in the future
- `[ ]` document how to add a new service and its owned ports
- `[ ]` document how to define a new exposure group
- `[ ]` document how to bind a service to an exposure group
- `[ ]` document how service subdomains are assigned
- `[ ]` document how temporary public exposure is enabled and reverted
- `[ ]` write a short operator quickstart

## Session Handoff Template

At the end of any implementation session, append or update the current state using this structure in the commit message, task note, or relevant progress document:

```text
Session summary:
- What changed
- What remains
- What is blocked

Files touched:
- path/to/file

Validation:
- tests run
- commands tried

Next recommended step:
- one concrete next task
```

## Next Recommended Start

The next implementation session should begin with:

1. define how `control` records installed `homebase` revision for each node
2. add a remote rollout command from `control` to one or more nodes
3. add node edit/remove commands
