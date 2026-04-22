# homebase Current State

## Snapshot

This document captures the currently implemented state of `homebase`.

Current build status:

- the `host` node is mostly built
- three VMs have been installed on the host
- baseline setup on each VM is nearly complete

Current VM names:

- `storage`
- `app`
- `llm-agents`

## Immediate Direction

The next operational step is to move the `homebase` working directory onto NAS-backed storage.

Current intent:

- move the `homebase` directory itself from the local home directory onto NAS storage
- treat the NAS-backed copy as the canonical working location
- perform ongoing work there so outputs, logs, and progress artifacts can be observed in one shared place

## Working Directory Policy

The repository and related work artifacts should follow these rules:

- the canonical `homebase` path should live on NAS-backed storage
- long-lived outputs, generated artifacts, and progress traces should be written there rather than onto local transient disks
- local nodes may expose the NAS location through a stable mount point or symlink for operator convenience
- automation should fail clearly if the NAS mount is unavailable rather than silently writing state to an unintended local path

Suggested practical pattern:

- keep the real directory on the NAS share
- preserve `/home/sun/homebase` as a symlink only if local tools still expect that path

## Near-Term Open Items

- choose the final NAS export path and client mount path
- define which nodes need read-write versus read-only access
- decide whether `homebase` automation outputs should live beside the repo or under a separate NAS subdirectory
- include the `homebase` NAS dataset or share in the snapshot and backup policy
