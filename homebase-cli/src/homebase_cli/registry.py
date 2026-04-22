"""Persistent node registry for the homebase CLI."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import tomllib

from homebase_cli.paths import LOCAL_CLI_ROOT


DEFAULT_REGISTRY_PATH = LOCAL_CLI_ROOT / "config" / "nodes.toml"


@dataclass(frozen=True)
class Node:
    """One registered node in the local homebase registry."""

    name: str
    parent: str | None = None
    kind: str = "node"
    address: str | None = None
    ssh_user: str | None = None
    description: str = ""
    runtime_hostname: str | None = None
    node_id: str | None = None
    platform: str | None = None
    client_port: int | None = None
    open_ports: tuple[int, ...] = ()
    services: tuple[str, ...] = ()

    @property
    def depth(self) -> int:
        """Return the hierarchy depth of the node."""
        return 0 if self.parent is None else self.name.count(".")


def registry_path(path: Path | None = None) -> Path:
    """Resolve the active registry path."""
    if path is not None:
        return path
    override = os.environ.get("HOMEBASE_REGISTRY_PATH")
    if override:
        return Path(override)
    return DEFAULT_REGISTRY_PATH


def load_nodes(path: Path | None = None) -> tuple[Node, ...]:
    """Load nodes from the registry file."""
    path = registry_path(path)
    if not path.exists():
        return ()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    raw_nodes = data.get("nodes", [])
    nodes: list[Node] = []
    for item in raw_nodes:
        values = dict(item)
        nodes.append(
            Node(
                name=str(values["name"]),
                parent=str(values["parent"]) if "parent" in values else None,
                kind=str(values.get("kind", "node")),
                address=str(values["address"]) if "address" in values else None,
                ssh_user=str(values["ssh_user"]) if "ssh_user" in values else None,
                description=str(values.get("description", "")),
                runtime_hostname=str(values["runtime_hostname"]) if "runtime_hostname" in values else None,
                node_id=str(values["node_id"]) if "node_id" in values else None,
                platform=str(values["platform"]) if "platform" in values else None,
                client_port=int(values["client_port"]) if "client_port" in values else None,
                open_ports=tuple(int(port) for port in values.get("open_ports", [])),
                services=tuple(str(service) for service in values.get("services", [])),
            )
        )
    return tuple(sorted(nodes, key=lambda item: item.name))


def save_nodes(nodes: tuple[Node, ...], path: Path | None = None) -> None:
    """Persist the registry as TOML."""
    path = registry_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# homebase node registry", ""]
    for node in nodes:
        lines.append("[[nodes]]")
        lines.append(f'name = "{node.name}"')
        if node.parent is not None:
            lines.append(f'parent = "{node.parent}"')
        lines.append(f'kind = "{node.kind}"')
        if node.address is not None:
            lines.append(f'address = "{node.address}"')
        if node.ssh_user is not None:
            lines.append(f'ssh_user = "{node.ssh_user}"')
        if node.description:
            escaped = node.description.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'description = "{escaped}"')
        if node.runtime_hostname is not None:
            lines.append(f'runtime_hostname = "{node.runtime_hostname}"')
        if node.node_id is not None:
            lines.append(f'node_id = "{node.node_id}"')
        if node.platform is not None:
            lines.append(f'platform = "{node.platform}"')
        if node.client_port is not None:
            lines.append(f"client_port = {node.client_port}")
        if node.open_ports:
            port_values = ", ".join(str(port) for port in node.open_ports)
            lines.append(f"open_ports = [{port_values}]")
        if node.services:
            service_values = ", ".join(f'"{service}"' for service in node.services)
            lines.append(f"services = [{service_values}]")
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def find_node(name: str, path: Path | None = None) -> Node | None:
    """Find a node by exact name."""
    normalized = name.strip()
    for node in load_nodes(path):
        if node.name == normalized:
            return node
    return None


def child_nodes(parent: str | None = None, path: Path | None = None) -> tuple[Node, ...]:
    """Return the direct children for one parent."""
    return tuple(node for node in load_nodes(path) if node.parent == parent)


def add_node(
    *,
    name: str,
    parent: str | None = None,
    kind: str = "node",
    address: str | None = None,
    ssh_user: str | None = None,
    description: str = "",
    runtime_hostname: str | None = None,
    node_id: str | None = None,
    platform: str | None = None,
    client_port: int | None = None,
    open_ports: tuple[int, ...] = (),
    services: tuple[str, ...] = (),
    path: Path | None = None,
) -> Node:
    """Add one node to the persistent registry."""
    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("node name cannot be empty")
    existing_nodes = load_nodes(path)
    if any(node.name == normalized_name for node in existing_nodes):
        raise ValueError(f"node already exists: {normalized_name}")
    normalized_parent = parent.strip() if parent is not None else None
    if normalized_parent and not any(node.name == normalized_parent for node in existing_nodes):
        raise ValueError(f"unknown parent node: {normalized_parent}")

    node = Node(
        name=normalized_name,
        parent=normalized_parent,
        kind=kind.strip() or "node",
        address=address.strip() if address else None,
        ssh_user=ssh_user.strip() if ssh_user else None,
        description=description.strip(),
        runtime_hostname=runtime_hostname.strip() if runtime_hostname else None,
        node_id=node_id.strip() if node_id else None,
        platform=platform.strip() if platform else None,
        client_port=client_port,
        open_ports=tuple(sorted(open_ports)),
        services=tuple(services),
    )
    save_nodes(existing_nodes + (node,), path=path)
    return node
