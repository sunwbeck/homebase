"""Helpers for reading the NAS-backed homebase documentation set."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from homebase_cli.paths import NAS_HOMEBASE


DOCS_ROOT = NAS_HOMEBASE / "docs"


@dataclass(frozen=True)
class DocEntry:
    """Metadata for a known document."""

    key: str
    title: str
    filename: str
    summary: str

    @property
    def path(self) -> Path:
        """Return the absolute path to the document."""
        return DOCS_ROOT / self.filename


DOCS = (
    DocEntry("index", "Docs Index", "docs-index.md", "Map of which document contains which topic."),
    DocEntry("current-state", "Current State", "current-state.md", "Current build state, VM inventory, and immediate next steps."),
    DocEntry("system-overview", "System Overview", "system-overview.md", "Overall structure, naming model, and CLI resource direction."),
    DocEntry("final-architecture", "Final Architecture", "final-architecture.md", "Target end-state architecture and operational model."),
    DocEntry("node-roles", "Node Roles", "node-roles.md", "Role boundaries and responsibilities for each node."),
    DocEntry("network-access", "Network and Access Policy", "network-and-access-policy.md", "Network layout, SSH policy, and remote access rules."),
    DocEntry("control-plane", "Control Plane", "control-plane.md", "Control-node responsibilities and CLI direction."),
    DocEntry("storage-backup", "Storage and Backup", "storage-and-backup.md", "Storage topology, NAS placement, and backup direction."),
    DocEntry("cli-plan", "CLI Development Plan", "homebase-cli-development-plan.md", "CLI goals, architecture, phases, and module direction."),
    DocEntry("cli-checklist", "CLI Checklist", "homebase-cli-checklist.md", "Execution checklist and handoff discipline for CLI work."),
    DocEntry("nas-migration", "NAS Migration", "nas-migration.md", "Runbook for moving the repo onto NAS-backed storage."),
)


def docs_root() -> Path:
    """Return the canonical NAS docs path."""
    return DOCS_ROOT


def list_docs() -> tuple[DocEntry, ...]:
    """Return all known docs in a stable order."""
    return DOCS


def get_doc(key: str) -> DocEntry | None:
    """Resolve a doc by key or filename stem."""
    normalized = key.strip().lower()
    for entry in DOCS:
        if normalized in {entry.key, entry.filename, entry.path.stem}:
            return entry
    return None


def read_doc(key: str) -> str:
    """Read a known document from disk."""
    entry = get_doc(key)
    if entry is None:
        raise KeyError(f"unknown doc: {key}")
    return entry.path.read_text(encoding="utf-8")
