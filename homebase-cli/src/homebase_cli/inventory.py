"""Ansible inventory helpers for registered nodes."""

from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile

from homebase_cli.registry import Node, find_node, load_nodes


def render_ansible_inventory(nodes: tuple[Node, ...]) -> str:
    """Render a minimal INI inventory from registered nodes."""
    lines = ["[homebase]"]
    for node in nodes:
        if not node.address:
            continue
        parts = [node.name, f"ansible_host={node.address}"]
        if node.ssh_user:
            parts.append(f"ansible_user={node.ssh_user}")
        lines.append(" ".join(parts))
    return "\n".join(lines) + "\n"


def write_ansible_inventory(path: Path) -> Path:
    """Write the current registry to an inventory file."""
    path.write_text(render_ansible_inventory(load_nodes()), encoding="utf-8")
    return path


def ansible_ping(node_name: str) -> subprocess.CompletedProcess[str]:
    """Run ansible ping against one registered node."""
    node = find_node(node_name)
    if node is None:
        raise ValueError(f"unknown node: {node_name}")
    if not node.address:
        raise ValueError(f"node has no address: {node_name}")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".ini", delete=False) as handle:
        handle.write(render_ansible_inventory(load_nodes()))
        inventory_path = handle.name
    return subprocess.run(
        ["ansible", node_name, "-i", inventory_path, "-m", "ping"],
        check=False,
        capture_output=True,
        text=True,
    )
