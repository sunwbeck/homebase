# homebase Current State

## Snapshot

`homebase` now uses the GitHub repository as the working source and keeps its planning docs inside the repo under `docs/`.

Current implementation direction:

- one CLI surface for both controller and managed nodes
- `connect` for pairing and discovery
- `daemon` for background runtime
- `service` for inspecting exposed services on nodes and groups
- GitHub-backed install and update flow through `homebase package`
- `homebase package` can now inspect or roll GitHub refs across the local node, selected nodes, selected groups, or the whole registered fleet
- repo-local docs are updated with the code, rather than keeping a separate NAS docs root
- managed nodes now have basic Windows profile and service support in addition to Linux

Current runtime naming:

- local runtime roles: `controller`, `managed`
- top-level nodes still center on `control`, `workstation`, and `host`
- host children currently remain `host.storage`, `host.app`, and `host.llm-agents`

## Immediate Direction

The near-term work is CLI cleanup and operator UX hardening, not repository relocation.

Current priority:

- make `status`, `node`, `group`, and `link` outputs clearly useful
- make root and subcommand `--help` output self-sufficient for both humans and LLMs
- keep one command grammar across controller and managed nodes
- keep background responsibilities under `daemon`
- keep service inspection under `service`
- prepare the controller runtime for upcoming reverse proxy work
- keep install and rollout behavior GitHub-ref driven
- keep status output focused on usable exposure and runtime information rather than operator label fields
- keep Windows managed-node support at the level of pairing, profile collection, and basic service start/stop until deeper Windows ops are designed

## Working Directory Policy

The checked-out GitHub repository is the active working copy.

Rules:

- keep `/home/sun/homebase` as the local operator checkout unless a different repo checkout is explicitly chosen
- treat `docs/` inside that repo as the planning and architecture reference
- do not depend on `/mnt/files/homebase/docs`
- generated local runtime state may live under the user home directory, but repository code and docs stay in git

## Near-Term Open Items

- tighten the purpose of `list`, `show`, and `status` across CLI object commands
- remove hidden legacy inventory mutation paths still left behind from earlier iterations
- define the `proxy` command group for reverse proxy and service exposure work
- add Git tags or releases so `homebase package versions` can show versioned targets instead of only the default branch
