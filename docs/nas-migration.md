# homebase NAS Migration

## Goal

Move the canonical `homebase` working directory from the local home directory onto NAS-backed storage without breaking existing local tooling.

This runbook assumes the NAS share is already exported and mounted on the client that will perform the migration.

## Preconditions

- choose the NAS-mounted destination path
- confirm the mounted filesystem has enough free space
- confirm the operator account has write permission on the destination
- stop any automation that may write into `homebase` during the move

Example variables:

```text
SOURCE=/home/sun/homebase
DEST_ROOT=<nas-mount-point>
DEST=$DEST_ROOT/homebase
BACKUP=/home/sun/homebase.pre-nas
```

## Recommended Procedure

### 1. Verify the destination mount

```bash
findmnt "$DEST_ROOT"
df -h "$DEST_ROOT"
```

### 2. Create the destination directory

```bash
mkdir -p "$DEST"
```

### 3. Copy the repository to NAS

```bash
rsync -a --info=progress2 "$SOURCE"/ "$DEST"/
```

### 4. Verify the copied tree

```bash
diff -rq "$SOURCE" "$DEST"
```

If the diff is clean, continue.

### 5. Preserve the local copy as rollback state

```bash
mv "$SOURCE" "$BACKUP"
```

### 6. Replace the old path with a symlink

```bash
ln -s "$DEST" "$SOURCE"
```

### 7. Validate the final path behavior

```bash
ls -ld "$SOURCE"
cd "$SOURCE" && pwd
test -f "$SOURCE/README.md"
```

## Rollback

If local tooling breaks after the symlink swap:

```bash
rm "$SOURCE"
mv "$BACKUP" "$SOURCE"
```

## Follow-Up Tasks

- include the NAS-backed `homebase` path in snapshot policy
- decide which nodes should mount the share
- define where generated logs, reports, and artifacts should live under the NAS path
- make automation check for mount availability before writing results
