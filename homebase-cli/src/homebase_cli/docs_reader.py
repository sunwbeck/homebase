"""Helpers for reading the homebase documentation set."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

REPO_DOC_CANDIDATES = (
    Path("/home/sun/homebase/docs"),
    Path(__file__).resolve().parents[3] / "docs",
)
GITHUB_DOCS_BASE_URL = "https://github.com/sunwbeck/homebase/blob/main/docs"


@dataclass(frozen=True)
class DocEntry:
    """Metadata for a known document."""

    key: str
    title: str
    filename: str
    summary: str

    @property
    def url(self) -> str:
        """Return the GitHub URL for the document."""
        return f"{GITHUB_DOCS_BASE_URL}/{self.filename}"


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


def _existing_docs_root(candidates: Iterable[Path]) -> Path | None:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def docs_root() -> Path | None:
    """Return the local docs path when one is available."""
    return _existing_docs_root(REPO_DOC_CANDIDATES)


def list_docs() -> tuple[DocEntry, ...]:
    """Return all known docs in a stable order."""
    return DOCS


def get_doc(key: str) -> DocEntry | None:
    """Resolve a doc by key or filename stem."""
    normalized = key.strip().lower()
    for entry in DOCS:
        filename_stem = Path(entry.filename).stem
        if normalized in {entry.key, entry.filename, filename_stem}:
            return entry
    return None


def read_doc(key: str) -> str:
    """Read a known document from disk when a local docs checkout exists."""
    entry = get_doc(key)
    if entry is None:
        raise KeyError(f"unknown doc: {key}")
    root = docs_root()
    if root is None:
        raise FileNotFoundError(f"local docs checkout not found for {entry.filename}")
    return (root / entry.filename).read_text(encoding="utf-8")
