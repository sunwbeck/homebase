"""Output helpers for terminal rendering."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

console = Console()


def print_docs_table(rows: list[tuple[str, str, str]]) -> None:
    """Render a docs overview table."""
    table = Table(title="Homebase Docs")
    table.add_column("Key", style="cyan")
    table.add_column("File", style="green")
    table.add_column("Summary")
    for key, filename, summary in rows:
        table.add_row(key, filename, summary)
    console.print(table)


def print_node_tree(rows: list[tuple[str, str]]) -> None:
    """Render one flat node tree view."""
    table = Table(title="Homebase Node Tree")
    table.add_column("Node", style="cyan")
    table.add_column("Kind", style="green")
    for name, kind in rows:
        table.add_row(name, kind)
    console.print(table)


def print_scan_table(rows: list[tuple[str, str, str, str, str, str, str]]) -> None:
    """Render discovered homebase clients from a scan."""
    table = Table(title="Homebase Client Scan")
    table.add_column("Address", style="cyan")
    show_known = any(known for _, known, _, _, _, _, _ in rows)

    if show_known:
        table.add_column("Known")
    table.add_column("Node")
    table.add_column("Hostname")
    table.add_column("Description")
    table.add_column("Platform")
    table.add_column("Version")
    for address, known, node_name, hostname, description, platform, version in rows:
        cells = [address]
        if show_known:
            cells.append(known)
        cells.append(node_name)
        cells.append(hostname)
        cells.append(description)
        cells.append(platform)
        cells.append(version)
        table.add_row(*cells)
    console.print(table)
