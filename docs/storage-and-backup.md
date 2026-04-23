# homebase Storage and Backup

## Primary Storage Node

Primary storage is planned on `host.storage`.

Planned stack:

- TrueNAS
- ZFS

## Disk Layout

Planned disk options:

- `4 TB x2` as ZFS mirror
- `4 TB x3` as RAIDZ1

This design makes `host.storage` the main data platform for `homebase`.

## Repository Placement

The `homebase` repository stays in git and is worked from a normal checked-out directory.

Operational intent:

- NAS-backed storage should be used for backups, snapshots, and large runtime artifacts when needed
- repository history, code, and docs continue to live in git
- the storage location should be covered by snapshots and the backup workflow

## Backup Plan

Current backup target:

- external `3 TB` HDD connected through DAS

Current backup cadence:

- monthly ZFS pool backup
- weekly snapshot backup

## Design Intent

- primary storage should be resilient
- snapshots should provide short-term recovery points
- external backup should provide an offline or semi-offline recovery copy

## Known Constraint

The `3 TB` external backup disk may become a limiting factor depending on actual stored data size and growth.

This should be validated before the storage plan is treated as final.
