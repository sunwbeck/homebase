# homebase

`homebase` is the source of truth for both:

- system planning and operational documentation
- the `homebase` CLI implementation

Repository layout:

- `docs/`: system and CLI planning documents
- `homebase-cli/`: Python CLI source tree
- `scripts/install-homebase.sh`: one-line first-install helper

## First Install

The first install path should stay copy-paste simple.

```bash
curl -fsSL https://raw.githubusercontent.com/sunwbeck/homebase/main/scripts/install-homebase.sh | bash
```

If a specific GitHub ref should be installed, use the same script with `--ref`:

```bash
curl -fsSL https://raw.githubusercontent.com/sunwbeck/homebase/main/scripts/install-homebase.sh | bash -s -- --ref main
```

Install behavior stays generic:

- this works from any directory without GitHub CLI authentication
- if a venv is active, install into that venv
- otherwise the install script creates or reuses `~/.local/share/homebase-cli/.venv`
- and links `hb` and `homebase` into `~/.local/bin`

## Version And Rollout Direction

GitHub is the source of truth for `homebase` version and rollout identity.

That means:

- `git` and GitHub decide which branch, tag, release, or commit should be installed
- `homebase` should not maintain its own parallel version registry
- `control` should track which `homebase` revision is installed on each managed node
- `control` should later orchestrate install and upgrade across other nodes

Planned CLI grammar:

```text
hb package versions
hb package status
hb package status <resource>
hb package install [<resource>] --ref <git-ref>
hb package upgrade [<resource>] --ref <git-ref>
hb package update [<resource>]
hb package install [<resource>] --ref <git-ref> --repo <git-url>
hb package upgrade [<resource>] --ref <git-ref> --repo <git-url>
```

Grammar notes:

- `package versions` lists GitHub releases or tags together with a short summary
- if no `<resource>` is given, operate on the current node
- `--ref` accepts a branch, tag, release tag, or commit SHA
- `--repo` defaults to `https://github.com/sunwbeck/homebase.git`
- `package install` can choose from listed GitHub versions when `--ref` is omitted
- `package upgrade` or `package update` should move to the latest available GitHub release, or the default branch if no release exists
- install or upgrade should follow normal Python environment behavior on the target node
- `package status` should show which Git ref or commit is installed on each managed node

What revision tracking means:

- each managed node reports which `homebase` Git revision is installed there
- `control` uses that to show which nodes match the desired target and which are behind

## Current Docs

- `docs/current-state.md`: current implementation status, active VM inventory, and immediate next steps
- `docs/docs-index.md`: short map of which document contains which topic
- `docs/nas-migration.md`: step-by-step runbook for moving the `homebase` directory onto NAS-backed storage
- `docs/system-overview.md`: current naming rules, system structure, and baseline architecture summary
- `docs/final-architecture.md`: target architecture for the full `homebase` system
- `docs/node-roles.md`: role, responsibility, and boundary definitions for each node and subnode
- `docs/network-and-access-policy.md`: network layout, access paths, and security policy baseline
- `docs/control-plane.md`: planned responsibilities and components for the `control` node
- `docs/storage-and-backup.md`: storage topology, ZFS layout, and backup strategy baseline
- `docs/homebase-cli-development-plan.md`: implementation plan, milestones, and delivery criteria for the `homebase` CLI
- `docs/homebase-cli-checklist.md`: execution checklist and handoff discipline for continuing CLI work across sessions or contributors
