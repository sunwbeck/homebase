# homebase Control Plane

## Purpose

The `control` node is the management and observability plane of `homebase`.

It is intended to stay online continuously and provide a single operational viewpoint for the rest of the system.

Confirmed current platform:

- Raspberry Pi 4 Model B Rev 1.2
- 4-core Cortex-A72 CPU at up to 1.5 GHz
- 4 GB RAM
- Debian 13 (`trixie`)
- root filesystem on a 64 GB-class SD card device (`mmcblk0`)

## Planned Components

- custom `homebase` CLI
- Ansible
- health-check scripts or services

Related services operated through the control plane:

- Prometheus on `host.app`
- Grafana on `host.app`
- one possible ingress provider for selected services on `host.app`

## Responsibilities

- discover and present the state of `homebase`
- orchestrate predefined operations on managed nodes
- orchestrate `homebase` install and upgrade on managed nodes
- present which `homebase` revision is installed on each managed node
- hold operator-defined role groups and state labels for registered nodes
- provide a structured interface for status inspection
- reduce the need for ad hoc direct administration
- act as one approved ingress and policy orchestration point for services that are intentionally routed through `control`

The preferred managed-node model is:

- `control` stays authoritative
- managed nodes run a thin `homebase` client
- clients self-report identity, local facts, and later heartbeat information
- `control` does not depend on LAN guessing as the long-term source of node identity
- `control` should store role-group definitions and node state labels in the registry it manages

Preferred rollout model:

- GitHub defines `homebase` version and rollout identity
- `control` selects the GitHub ref that should be installed
- managed nodes report which `homebase` revision is currently installed
- the operator should be able to roll one canary node first and then expand rollout from `control`
- the operator should not need to log into every node just to choose a version manually

## `homebase` CLI Direction

The future CLI should be:

- hierarchical
- discoverable
- completion-friendly
- operationally informative

Example resource paths:

```text
control
workstation
host
host.storage
host.app
host.llm-agents
```

Example commands:

```text
homebase status host
homebase status host.app
homebase restart host.llm-agents
homebase ssh host.storage
```

Desired operator experience:

- targets should be discoverable through completion
- resource hierarchy should be visible while typing
- the user should be able to learn the system structure from the CLI itself
- the user should be able to inspect service exposure in grouped form rather than by raw port list alone
- the user should be able to change service exposure through a stable declarative command surface

## Observability Direction

Prometheus and Grafana are planned so the operator can inspect system data across devices.

The intended placement is:

- `control` hosts the operator entry point, CLI, and lightweight health-check logic
- `host.app` hosts Prometheus, Grafana, and other storage-heavy observability services

This keeps `control` lightweight on Raspberry Pi hardware while preserving `control` as the recovery entry point if `host.app` is degraded.

Practical implication for the current hardware:

- `control` is appropriate for the CLI, Ansible, SSH routing, and lightweight health logic
- `control` is also appropriate for a lightweight reverse proxy used as one ingress bridge to internal services
- `control` should not be the primary write-heavy storage location for time-series metrics or long-lived logs
- `control` should not be the canonical long-lived storage location for the `homebase` repository or generated operation artifacts

Practical implication for node telemetry:

- lightweight identity and state should come from the node client
- long-lived metrics should be scraped or stored through Prometheus on `host.app`
- `control` should aggregate and present status without becoming the metrics database

Target observability scope:

- node health
- CPU and memory usage
- storage health
- service availability
- backup and snapshot recency

## Privilege Elevation Direction

The control plane should assume that some real operational work will require privileged commands on managed nodes.

This is especially important for exception handling:

- the operator may be away from home
- a managed node may need urgent recovery through SSH
- the exact required `sudo` command may not be predictable ahead of time

Because of that, the long-term model should not rely only on a fixed whitelist of pre-approved `NOPASSWD` commands.

That whitelist remains useful for routine read-only inspection, but it is not sufficient as the only escalation model.

Preferred direction:

- routine inspection should use predefined low-risk commands where practical
- exceptional remote recovery should support operator-approved temporary privilege elevation
- the system should prefer a short-lived elevation window over permanently broad privileged access
- the operator should not need to disclose the raw `sudo` password in chat or persist it in ad hoc scripts

Desired properties of the exception path:

- the operator explicitly approves elevation when needed
- elevation can be granted for a short TTL such as a few minutes
- the resulting privileged session is limited in time and easy to audit
- the model should work even when the exact recovery command was not predicted in advance

Implementation options may include:

- a local root-authenticated session opened by the operator and then used by the CLI
- a local privileged helper on the managed node or on `control`
- a secret-manager-backed approval flow that keeps password handling outside normal chat and shell history
- a more formal just-in-time elevation mechanism if the system grows beyond simple home use

The key requirement is that `homebase` must support unplanned recovery work without forcing either of these tradeoffs:

- broad permanent `NOPASSWD` access
- manual password handling through ad hoc remote shell commands

## Network Exposure Model

The preferred long-term direction is not to hardcode a small built-in set of exposure classes.

Instead, `homebase` should model network exposure through user-defined named groups and explicit service bindings.

Core model:

- `node`: a machine or VM such as `control` or `host.app`
- `service`: a named workload on a node
- `port`: an exposed port owned by a service
- `exposure group`: a user-defined grouping that describes who can reach a service and through which ingress path
- `binding`: the attachment of a service to an exposure group

Design intent:

- do not force the operator into preselected policy names
- allow the operator to define environment-specific group names that match actual usage
- allow new ingress patterns to be added without redesigning the CLI grammar
- keep the system modular enough that new nodes, services, and ingress paths can be introduced later

The `control` node remains important in this model, but as one provider inside the exposure system rather than as a mandatory universal choke point for every service.

Implications for `control`:

- `control` may host a reverse proxy for services whose bindings route through it
- `control` should expose status about those bindings and their realized state
- `control` should not be assumed to carry every possible service flow in the system
- `control` should remain lightweight enough that heavy or public traffic can be realized through another path if needed

Recommended first implementation:

- run a reverse proxy on `control`
- use subdomain-based routing such as `nextcloud.sunwoobeck.com` and `yacreader.sunwoobeck.com`
- keep the default service mode private to the tailnet path
- let remote clients reach those subdomains only when they are on the same tailnet and the names resolve to the ingress on `control`

Candidate reverse proxy options still include:

- Caddy for simple host and path routing
- Nginx if more manual control is preferred

These ingress tools should be treated as realization mechanisms for exposure bindings rather than as the policy model itself.

## Internal And Temporary Public Modes

The intended operator workflow includes both a normal private mode and a temporary public mode for selected services.

Expected default mode:

- a service such as Nextcloud is reachable only through the private ingress path
- the operator joins the tailnet from a trusted device
- the service subdomain resolves to the ingress path on `control`
- the backend on `host.app` remains private

Expected temporary exception mode:

- the operator may need to access a service from a machine where Tailscale cannot reasonably be installed
- the operator can use the `homebase` CLI on `control` to switch that service to a temporary public ingress realization
- after the task is complete, the operator can switch the service back to its private mode

Design rules for this mode switch:

- the switch must be explicit and service-specific
- the private mode remains the default and preferred steady state
- the public mode should be easy to revert
- the public mode should support a time limit or scheduled auto-revert where practical
- public mode should support an extra protection layer such as reverse-proxy auth or edge access control when feasible

The long-term model should treat this as switching bindings or realization targets, not as manually rewriting ad hoc firewall rules every time.

## CLI Direction For Network Exposure

The future CLI should grow a network-oriented surface that makes the exposure model inspectable and changeable.

Desired command direction:

```text
homebase network status
homebase network status host.app
homebase network status host.app --grouped
homebase network group create <name>
homebase network group rule add <name> ...
homebase network bind <resource> <service> <group>
homebase network explain <resource> <service>
homebase network reconcile
homebase network expose <resource> <service> <mode>
homebase network expose <resource> <service> <mode> --ttl <duration>
```

Desired output characteristics:

- show services grouped by user-defined exposure group
- show which ports belong to which service
- show which ingress path currently realizes the binding
- show the difference between declared intent and actual host state when they diverge

The primary operator view should not be an unstructured raw port list.

It should explain:

- which service owns a port
- who can currently reach it
- through which node or ingress path it is reachable
- whether the realized firewall and proxy state matches the declared binding
- whether the service is currently in private-only or temporary public mode
- which subdomain is active for the current mode
- whether a temporary exposure is scheduled to auto-revert

## Open Decisions

- the exact health-check implementation model
- the exact command set and UX of the `homebase` CLI
- the exact reverse proxy implementation and routing convention for services bound through `control`
- the exact privilege elevation mechanism for exception handling and emergency remote recovery
- the storage format for user-defined exposure groups and service bindings
- the first realization backends for turning declared exposure into firewall and proxy state
- the exact implementation of temporary public exposure and automatic rollback
