# homebase-cli

CLI source tree inside the GitHub-backed `homebase` repository.

Development direction:

- keep docs and CLI code in one repository
- use GitHub refs as the source of truth for install and upgrade
- build around real node identity rather than guessed LAN metadata

## Initial Decisions

- language: Python 3.11+
- CLI framework: Typer
- terminal rendering: Rich
- tests: pytest
- config format: TOML

## Planned Layout

```text
homebase-cli/
  pyproject.toml
  README.md
  docs/
  src/homebase_cli/
  tests/
```

See `docs/stack-and-structure.md` for the initial architecture decision.

## Current Working Slice

The current real feature is agent-first node discovery and registration.

Useful commands:

- primary command: `homebase`
- short alias: `hb`
- on a target node: `homebase client code --refresh`
- on a target node: `homebase client serve`
- on the control node: `homebase node scan 192.168.219.0/24`
- register a discovered node: `homebase node add`
- `homebase ls`
- `homebase info host`
- `homebase node status`

Current pairing flow:

1. run `homebase client code --refresh` on the target node and note the 8-digit code
2. run `homebase client serve` on the target node
3. run `homebase node scan ...` on the control node
4. run `homebase node add`
5. choose the discovered device and enter the 8-digit code from the target node

Before pairing, scan only stores minimal discovery data. Full ports and services are fetched only after successful pairing.

## Development And Rollout Direction

The working model remains `control` plus a thin client on each managed node.

The deployment direction is now:

- GitHub is the source of truth for `homebase` version and rollout state
- `git` decides which commit, branch, tag, or release should be installed
- `homebase` should not try to become its own package repository or version registry
- `control` should track which `homebase` revision is installed on each managed node
- `control` should be able to request install or upgrade across multiple nodes without the operator SSHing into each one manually

Recommended operator workflow:

1. make and validate changes locally
2. commit and push to GitHub
3. choose one GitHub ref as the rollout target
4. use `control` to see which nodes are behind or ahead of that target
5. use `control` to roll one canary node first
6. expand rollout to the remaining nodes after the canary succeeds

Current useful commands:

- configure this node once: `homebase init`
- show or change the stored role: `homebase role`
- list or manage selectable roles: `homebase roles`
- local loopback validation: `homebase dev self-test`
- on a target node: `homebase client code --refresh`
- on a target node: `homebase client serve`
- on `control`: `homebase node scan`
- on `control`: `homebase node add`

Planned `package` grammar:

```text
homebase package versions
homebase package status
homebase package status <resource>
homebase package install [<resource>] --ref <git-ref>
homebase package upgrade [<resource>] --ref <git-ref>
homebase package update [<resource>]
homebase package install [<resource>] --ref <git-ref> --repo <git-url>
homebase package upgrade [<resource>] --ref <git-ref> --repo <git-url>
```

Meaning:

- `package versions` lists GitHub releases or tags with a short summary
- no resource means the current node
- `--ref` accepts branch, tag, or commit SHA
- `--repo` defaults to `https://github.com/sunwbeck/homebase.git`
- `package install` can choose from listed GitHub versions when `--ref` is omitted
- `package upgrade` and `package update` move to the latest available GitHub release or default branch
- install on a node should follow normal Python environment behavior on that node
- `package status` should later show which revision is installed on each managed node
