"""Ansible inventory helpers for registered nodes."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile

from homebase_cli.paths import LOCAL_CLI_ROOT
from homebase_cli.registry import Node, RoleGroup, find_node, load_nodes, load_role_groups


DEFAULT_ANSIBLE_INVENTORY_PATH = LOCAL_CLI_ROOT / "config" / "inventory.yml"


def ansible_inventory_path(path: Path | None = None) -> Path:
    """Resolve the active ansible inventory path."""
    if path is not None:
        return path
    override = os.environ.get("HOMEBASE_ANSIBLE_INVENTORY_PATH")
    if override:
        return Path(override)
    return DEFAULT_ANSIBLE_INVENTORY_PATH


def render_ansible_inventory(nodes: tuple[Node, ...], role_groups: tuple[RoleGroup, ...] | None = None) -> str:
    """Render an ansible-style YAML inventory from registered nodes and groups."""
    if role_groups is None:
        role_groups = ()
    lines = ["all:", "  children:", "    homebase:", "      hosts:"]
    if not nodes:
        lines.append("        {}")
    for node in nodes:
        lines.append(f"        {node.name}:")
        lines.append(f"          homebase_role: {node.runtime_role}")
        lines.append(f"          homebase_kind: {node.kind}")
        if node.address:
            lines.append(f"          ansible_host: {node.address}")
        if node.ssh_user:
            lines.append(f"          ansible_user: {node.ssh_user}")
        if node.parent:
            lines.append(f"          homebase_parent: {node.parent}")
        if node.description:
            lines.append(f'          homebase_description: "{node.description}"')
        if node.role_groups:
            lines.append("          homebase_groups:")
            for group in node.role_groups:
                lines.append(f"            - {group}")
    if role_groups:
        lines.append("    homebase_groups:")
        lines.append("      children:")
        for group in role_groups:
            lines.append(f"        {group.name}:")
            if group.description:
                lines.append("          vars:")
                lines.append(f'            homebase_description: "{group.description}"')
            if group.members:
                lines.append("          children:")
                for member in group.members:
                    lines.append(f"            {member}: {{}}")
            assigned_nodes = [node.name for node in nodes if group.name in node.role_groups]
            if assigned_nodes:
                lines.append("          hosts:")
                for node_name in assigned_nodes:
                    lines.append(f"            {node_name}: {{}}")
            if not group.description and not group.members and not assigned_nodes:
                lines.append("          {}")
    return "\n".join(lines) + "\n"


def write_ansible_inventory(path: Path | None = None) -> Path:
    """Write the current registry to an ansible YAML inventory file."""
    target = ansible_inventory_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_ansible_inventory(load_nodes(), load_role_groups()), encoding="utf-8")
    return target


def open_ansible_inventory(editor: str | None = None, path: Path | None = None) -> Path:
    """Open the ansible inventory file in the configured editor."""
    target = write_ansible_inventory(path)
    chosen_editor = editor or os.environ.get("EDITOR") or "vi"
    subprocess.run([chosen_editor, str(target)], check=False)
    return target


def ansible_ping(node_name: str) -> subprocess.CompletedProcess[str]:
    """Run ansible ping against one registered node."""
    node = find_node(node_name)
    if node is None:
        raise ValueError(f"unknown node: {node_name}")
    if not node.address:
        raise ValueError(f"node has no address: {node_name}")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".yml", delete=False) as handle:
        handle.write(render_ansible_inventory(load_nodes(), load_role_groups()))
        inventory_path = handle.name
    return subprocess.run(
        ["ansible", node_name, "-i", inventory_path, "-m", "ping"],
        check=False,
        capture_output=True,
        text=True,
    )
