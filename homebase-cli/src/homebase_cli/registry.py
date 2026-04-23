"""Persistent node registry for the homebase CLI."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import tomllib

from homebase_cli.paths import LOCAL_CLI_ROOT


DEFAULT_REGISTRY_PATH = LOCAL_CLI_ROOT / "config" / "nodes.toml"
NODE_RUNTIME_ROLES = ("controller", "managed")


@dataclass(frozen=True)
class Node:
    """One registered node in the local homebase registry."""

    name: str
    parent: str | None = None
    kind: str = "node"
    runtime_role: str = "managed"
    address: str | None = None
    ssh_user: str | None = None
    description: str = ""
    runtime_hostname: str | None = None
    node_id: str | None = None
    platform: str | None = None
    client_port: int | None = None
    open_ports: tuple[int, ...] = ()
    services: tuple[str, ...] = ()
    exposed_endpoints: tuple[tuple[int, str, str | None], ...] = ()
    service_records: tuple[tuple[str, str, int | None, str, str], ...] = ()
    role_groups: tuple[str, ...] = ()

    @property
    def depth(self) -> int:
        """Return the hierarchy depth of the node."""
        return 0 if self.parent is None else self.name.count(".")


@dataclass(frozen=True)
class RoleGroup:
    """One named group used to organize nodes and other groups."""

    name: str
    description: str = ""
    members: tuple[str, ...] = ()


def normalize_node_runtime_role(value: str | None, *, kind: str | None = None) -> str:
    """Normalize one node runtime role from saved data or kind."""
    normalized = (value or "").strip().lower()
    if normalized == "client":
        normalized = "managed"
    if normalized == "control":
        normalized = "controller"
    if normalized in NODE_RUNTIME_ROLES:
        return normalized
    inferred_kind = (kind or "").strip().lower()
    if inferred_kind in {"control", "controller"}:
        return "controller"
    return "managed"


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
        exposed_entries: list[tuple[int, str, str | None]] = []
        service_entries: list[tuple[str, str, int | None, str, str]] = []
        for raw_entry in values.get("exposed_endpoints", []):
            entry = str(raw_entry).strip()
            if not entry:
                continue
            port_text, _, remainder = entry.partition("|")
            purpose_text, _, owner_text = remainder.partition("|")
            try:
                port_value = int(port_text.strip())
            except ValueError:
                continue
            purpose_value = purpose_text.strip() or str(port_value)
            owner_value = owner_text.strip() or None
            exposed_entries.append((port_value, purpose_value, owner_value))
        for raw_entry in values.get("service_records", []):
            entry = str(raw_entry).strip()
            if not entry:
                continue
            name_text, _, remainder = entry.partition("|")
            state_text, _, remainder = remainder.partition("|")
            pid_text, _, remainder = remainder.partition("|")
            kind_text, _, description_text = remainder.partition("|")
            name_value = name_text.strip()
            if not name_value:
                continue
            pid_value = int(pid_text.strip()) if pid_text.strip().isdigit() else None
            service_entries.append(
                (
                    name_value,
                    state_text.strip() or "unknown",
                    pid_value,
                    kind_text.strip() or "service",
                    description_text.strip(),
                )
            )
        nodes.append(
            Node(
                name=str(values["name"]),
                parent=str(values["parent"]) if "parent" in values else None,
                kind=str(values.get("kind", "node")),
                runtime_role=normalize_node_runtime_role(
                    str(values.get("runtime_role", "")).strip() or None,
                    kind=str(values.get("kind", "node")),
                ),
                address=str(values["address"]) if "address" in values else None,
                ssh_user=str(values["ssh_user"]) if "ssh_user" in values else None,
                description=str(values.get("description", "")),
                runtime_hostname=str(values["runtime_hostname"]) if "runtime_hostname" in values else None,
                node_id=str(values["node_id"]) if "node_id" in values else None,
                platform=str(values["platform"]) if "platform" in values else None,
                client_port=int(values["client_port"]) if "client_port" in values else None,
                open_ports=tuple(int(port) for port in values.get("open_ports", [])),
                services=tuple(str(service) for service in values.get("services", [])),
                exposed_endpoints=tuple(sorted(exposed_entries, key=lambda item: item[0])),
                service_records=tuple(sorted(service_entries, key=lambda item: (item[3], item[0]))),
                role_groups=tuple(str(name) for name in values.get("role_groups", [])),
            )
        )
    return tuple(sorted(nodes, key=lambda item: item.name))


def load_role_groups(path: Path | None = None) -> tuple[RoleGroup, ...]:
    """Load role groups from the registry file."""
    path = registry_path(path)
    if not path.exists():
        return ()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    raw_groups = data.get("role_groups", [])
    groups: list[RoleGroup] = []
    for item in raw_groups:
        values = dict(item)
        name = str(values.get("name", "")).strip()
        if not name:
            continue
        groups.append(
            RoleGroup(
                name=name,
                description=str(values.get("description", "")),
                members=tuple(str(member).strip() for member in values.get("members", []) if str(member).strip()),
            )
        )
    return tuple(sorted(groups, key=lambda item: item.name))


def _save_registry(nodes: tuple[Node, ...], role_groups: tuple[RoleGroup, ...], path: Path | None = None) -> None:
    """Persist the full registry as TOML."""
    path = registry_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# homebase node registry", ""]
    for node in nodes:
        lines.append("[[nodes]]")
        lines.append(f'name = "{node.name}"')
        if node.parent is not None:
            lines.append(f'parent = "{node.parent}"')
        lines.append(f'kind = "{node.kind}"')
        lines.append(f'runtime_role = "{node.runtime_role}"')
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
        if node.exposed_endpoints:
            endpoint_values = ", ".join(
                f'"{port}|{purpose}|{owner or ""}"' for port, purpose, owner in node.exposed_endpoints
            )
            lines.append(f"exposed_endpoints = [{endpoint_values}]")
        if node.service_records:
            service_values = ", ".join(
                f'"{name}|{state}|{pid or ""}|{kind}|{description}"'
                for name, state, pid, kind, description in node.service_records
            )
            lines.append(f"service_records = [{service_values}]")
        if node.role_groups:
            group_values = ", ".join(f'"{group}"' for group in node.role_groups)
            lines.append(f"role_groups = [{group_values}]")
        lines.append("")
    for group in role_groups:
        lines.append("[[role_groups]]")
        lines.append(f'name = "{group.name}"')
        if group.description:
            escaped_description = group.description.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'description = "{escaped_description}"')
        if group.members:
            member_values = ", ".join(f'"{member}"' for member in group.members)
            lines.append(f"members = [{member_values}]")
        else:
            lines.append("members = []")
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def save_nodes(nodes: tuple[Node, ...], path: Path | None = None) -> None:
    """Persist nodes while preserving role-group definitions."""
    _save_registry(nodes, load_role_groups(path), path)


def save_role_groups(role_groups: tuple[RoleGroup, ...], path: Path | None = None) -> None:
    """Persist role groups while preserving node entries."""
    _save_registry(load_nodes(path), role_groups, path)


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
    runtime_role: str | None = None,
    runtime_hostname: str | None = None,
    node_id: str | None = None,
    platform: str | None = None,
    client_port: int | None = None,
    open_ports: tuple[int, ...] = (),
    services: tuple[str, ...] = (),
    exposed_endpoints: tuple[tuple[int, str, str | None], ...] = (),
    service_records: tuple[tuple[str, str, int | None, str, str], ...] = (),
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
        runtime_role=normalize_node_runtime_role(runtime_role, kind=kind),
        address=address.strip() if address else None,
        ssh_user=ssh_user.strip() if ssh_user else None,
        description=description.strip(),
        runtime_hostname=runtime_hostname.strip() if runtime_hostname else None,
        node_id=node_id.strip() if node_id else None,
        platform=platform.strip() if platform else None,
        client_port=client_port,
        open_ports=tuple(sorted(open_ports)),
        services=tuple(services),
        exposed_endpoints=tuple(sorted(exposed_endpoints, key=lambda item: item[0])),
        service_records=tuple(sorted(service_records, key=lambda item: (item[3], item[0]))),
        role_groups=(),
    )
    save_nodes(existing_nodes + (node,), path=path)
    return node


def rename_node(name: str, new_name: str, path: Path | None = None) -> Node:
    """Rename one node and update any direct children that point to it."""
    normalized_name = name.strip()
    normalized_new_name = new_name.strip()
    if not normalized_new_name:
        raise ValueError("new node name cannot be empty")
    nodes = load_nodes(path)
    current = next((node for node in nodes if node.name == normalized_name), None)
    if current is None:
        raise ValueError(f"unknown node: {normalized_name}")
    if normalized_new_name != normalized_name and any(node.name == normalized_new_name for node in nodes):
        raise ValueError(f"node already exists: {normalized_new_name}")
    updated_nodes: list[Node] = []
    renamed: Node | None = None
    for node in nodes:
        if node.name == normalized_name:
            renamed = Node(
                name=normalized_new_name,
                parent=node.parent,
                kind=node.kind,
                runtime_role=node.runtime_role,
                address=node.address,
                ssh_user=node.ssh_user,
                description=node.description,
                runtime_hostname=node.runtime_hostname,
                node_id=node.node_id,
                platform=node.platform,
                client_port=node.client_port,
                open_ports=node.open_ports,
                services=node.services,
                exposed_endpoints=node.exposed_endpoints,
                service_records=node.service_records,
                role_groups=node.role_groups,
            )
            updated_nodes.append(renamed)
            continue
        updated_nodes.append(
            Node(
                name=node.name,
                parent=normalized_new_name if node.parent == normalized_name else node.parent,
                kind=node.kind,
                runtime_role=node.runtime_role,
                address=node.address,
                ssh_user=node.ssh_user,
                description=node.description,
                runtime_hostname=node.runtime_hostname,
                node_id=node.node_id,
                platform=node.platform,
                client_port=node.client_port,
                open_ports=node.open_ports,
                services=node.services,
                exposed_endpoints=node.exposed_endpoints,
                service_records=node.service_records,
                role_groups=node.role_groups,
            )
        )
    save_nodes(tuple(updated_nodes), path)
    assert renamed is not None
    return renamed


def remove_node(name: str, path: Path | None = None) -> Node:
    """Remove one node from the registry and detach any direct children."""
    normalized_name = name.strip()
    nodes = load_nodes(path)
    current = next((node for node in nodes if node.name == normalized_name), None)
    if current is None:
        raise ValueError(f"unknown node: {normalized_name}")
    updated_nodes: list[Node] = []
    for node in nodes:
        if node.name == normalized_name:
            continue
        updated_nodes.append(
            Node(
                name=node.name,
                parent=None if node.parent == normalized_name else node.parent,
                kind=node.kind,
                runtime_role=node.runtime_role,
                address=node.address,
                ssh_user=node.ssh_user,
                description=node.description,
                runtime_hostname=node.runtime_hostname,
                node_id=node.node_id,
                platform=node.platform,
                client_port=node.client_port,
                open_ports=node.open_ports,
                services=node.services,
                exposed_endpoints=node.exposed_endpoints,
                service_records=node.service_records,
                role_groups=node.role_groups,
            )
        )
    save_nodes(tuple(updated_nodes), path)
    return current


def set_node_runtime_role(name: str, runtime_role: str, path: Path | None = None) -> Node:
    """Set one node runtime role to controller or managed."""
    normalized_name = name.strip()
    normalized_runtime_role = normalize_node_runtime_role(runtime_role)
    nodes = load_nodes(path)
    current = next((node for node in nodes if node.name == normalized_name), None)
    if current is None:
        raise ValueError(f"unknown node: {normalized_name}")
    updated_nodes: list[Node] = []
    updated: Node | None = None
    for node in nodes:
        if node.name != normalized_name:
            updated_nodes.append(node)
            continue
        updated = Node(
            name=node.name,
            parent=node.parent,
            kind=node.kind,
            runtime_role=normalized_runtime_role,
            address=node.address,
            ssh_user=node.ssh_user,
            description=node.description,
            runtime_hostname=node.runtime_hostname,
            node_id=node.node_id,
            platform=node.platform,
            client_port=node.client_port,
            open_ports=node.open_ports,
            services=node.services,
            exposed_endpoints=node.exposed_endpoints,
            service_records=node.service_records,
            role_groups=node.role_groups,
        )
        updated_nodes.append(updated)
    save_nodes(tuple(updated_nodes), path)
    assert updated is not None
    return updated


def set_node_description(name: str, description: str, path: Path | None = None) -> Node:
    """Set one node description."""
    normalized_name = name.strip()
    nodes = load_nodes(path)
    current = next((node for node in nodes if node.name == normalized_name), None)
    if current is None:
        raise ValueError(f"unknown node: {normalized_name}")
    updated_nodes: list[Node] = []
    updated: Node | None = None
    for node in nodes:
        if node.name != normalized_name:
            updated_nodes.append(node)
            continue
        updated = Node(
            name=node.name,
            parent=node.parent,
            kind=node.kind,
            runtime_role=node.runtime_role,
            address=node.address,
            ssh_user=node.ssh_user,
            description=description.strip(),
            runtime_hostname=node.runtime_hostname,
            node_id=node.node_id,
            platform=node.platform,
            client_port=node.client_port,
            open_ports=node.open_ports,
            services=node.services,
            exposed_endpoints=node.exposed_endpoints,
            service_records=node.service_records,
            role_groups=node.role_groups,
        )
        updated_nodes.append(updated)
    save_nodes(tuple(updated_nodes), path)
    assert updated is not None
    return updated


def ensure_local_node(
    name: str,
    runtime_role: str,
    *,
    runtime_hostname: str | None = None,
    description: str = "",
    address: str | None = None,
    platform: str | None = None,
    client_port: int | None = None,
    open_ports: tuple[int, ...] = (),
    services: tuple[str, ...] = (),
    exposed_endpoints: tuple[tuple[int, str, str | None], ...] = (),
    service_records: tuple[tuple[str, str, int | None, str, str], ...] = (),
    previous_name: str | None = None,
    path: Path | None = None,
) -> Node:
    """Ensure the current local installation has one registry entry."""
    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("node name cannot be empty")
    nodes = load_nodes(path)
    existing = next((node for node in nodes if node.name == normalized_name), None)
    if existing is None and previous_name:
        previous = next((node for node in nodes if node.name == previous_name.strip()), None)
        if previous is not None:
            existing = rename_node(previous.name, normalized_name, path=path)
            nodes = load_nodes(path)
    if existing is None:
        kind = "controller" if normalize_node_runtime_role(runtime_role) == "controller" else "node"
        return add_node(
            name=normalized_name,
            kind=kind,
            runtime_role=runtime_role,
            runtime_hostname=runtime_hostname,
            description=description,
            address=address,
            platform=platform,
            client_port=client_port,
            open_ports=open_ports,
            services=services,
            exposed_endpoints=exposed_endpoints,
            service_records=service_records,
            path=path,
        )
    updated_nodes: list[Node] = []
    updated: Node | None = None
    for node in nodes:
        if node.name != normalized_name:
            updated_nodes.append(node)
            continue
        updated = Node(
            name=node.name,
            parent=node.parent,
            kind="controller" if normalize_node_runtime_role(runtime_role) == "controller" else node.kind,
            runtime_role=normalize_node_runtime_role(runtime_role, kind=node.kind),
            address=address or node.address,
            ssh_user=node.ssh_user,
            description=description.strip() or node.description,
            runtime_hostname=runtime_hostname or node.runtime_hostname,
            node_id=node.node_id,
            platform=platform or node.platform,
            client_port=client_port if client_port is not None else node.client_port,
            open_ports=tuple(sorted(open_ports)) if open_ports else node.open_ports,
            services=tuple(services) if services else node.services,
            exposed_endpoints=tuple(sorted(exposed_endpoints, key=lambda item: item[0])) if exposed_endpoints else node.exposed_endpoints,
            service_records=tuple(sorted(service_records, key=lambda item: (item[3], item[0]))) if service_records else node.service_records,
            role_groups=node.role_groups,
        )
        updated_nodes.append(updated)
    save_nodes(tuple(updated_nodes), path)
    assert updated is not None
    return updated


def add_role_group(
    *,
    name: str,
    description: str = "",
    path: Path | None = None,
) -> RoleGroup:
    """Add one role group to the registry."""
    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("group name cannot be empty")
    groups = load_role_groups(path)
    if any(group.name == normalized_name for group in groups):
        raise ValueError(f"group already exists: {normalized_name}")
    group = RoleGroup(
        name=normalized_name,
        description=description.strip(),
        members=(),
    )
    save_role_groups(groups + (group,), path)
    return group


def remove_role_group(name: str, path: Path | None = None) -> None:
    """Remove one role group from the registry."""
    normalized_name = name.strip()
    groups = load_role_groups(path)
    if any(normalized_name in group.members for group in groups):
        raise ValueError(f"group is still linked from another group: {normalized_name}")
    filtered = tuple(group for group in groups if group.name != normalized_name)
    if len(filtered) == len(groups):
        raise ValueError(f"unknown group: {normalized_name}")
    nodes = tuple(
        Node(
            name=node.name,
            parent=node.parent,
            kind=node.kind,
            runtime_role=node.runtime_role,
            address=node.address,
            ssh_user=node.ssh_user,
            description=node.description,
            runtime_hostname=node.runtime_hostname,
            node_id=node.node_id,
            platform=node.platform,
            client_port=node.client_port,
            open_ports=node.open_ports,
            services=node.services,
            exposed_endpoints=node.exposed_endpoints,
            service_records=node.service_records,
            role_groups=tuple(item for item in node.role_groups if item != normalized_name),
        )
        for node in load_nodes(path)
    )
    _save_registry(nodes, filtered, path)


def rename_role_group(name: str, new_name: str, path: Path | None = None) -> RoleGroup:
    """Rename one role group and update links and node assignments."""
    normalized_name = name.strip()
    normalized_new_name = new_name.strip()
    if not normalized_new_name:
        raise ValueError("new group name cannot be empty")
    groups = load_role_groups(path)
    current = next((group for group in groups if group.name == normalized_name), None)
    if current is None:
        raise ValueError(f"unknown group: {normalized_name}")
    if normalized_new_name != normalized_name and any(group.name == normalized_new_name for group in groups):
        raise ValueError(f"group already exists: {normalized_new_name}")
    updated_groups: list[RoleGroup] = []
    renamed: RoleGroup | None = None
    for group in groups:
        group_name = normalized_new_name if group.name == normalized_name else group.name
        members = tuple(normalized_new_name if member == normalized_name else member for member in group.members)
        rebuilt = RoleGroup(name=group_name, description=group.description, members=members)
        if group.name == normalized_name:
            renamed = rebuilt
        updated_groups.append(rebuilt)
    updated_nodes = tuple(
        Node(
            name=node.name,
            parent=node.parent,
            kind=node.kind,
            runtime_role=node.runtime_role,
            address=node.address,
            ssh_user=node.ssh_user,
            description=node.description,
            runtime_hostname=node.runtime_hostname,
            node_id=node.node_id,
            platform=node.platform,
            client_port=node.client_port,
            open_ports=node.open_ports,
            services=node.services,
            exposed_endpoints=node.exposed_endpoints,
            service_records=node.service_records,
            role_groups=tuple(normalized_new_name if item == normalized_name else item for item in node.role_groups),
        )
        for node in load_nodes(path)
    )
    _save_registry(updated_nodes, tuple(updated_groups), path)
    assert renamed is not None
    return renamed


def set_role_group_description(name: str, description: str, path: Path | None = None) -> RoleGroup:
    """Set one role group description."""
    normalized_name = name.strip()
    groups = load_role_groups(path)
    updated_groups: list[RoleGroup] = []
    updated: RoleGroup | None = None
    for group in groups:
        if group.name != normalized_name:
            updated_groups.append(group)
            continue
        updated = RoleGroup(name=group.name, description=description.strip(), members=group.members)
        updated_groups.append(updated)
    if updated is None:
        raise ValueError(f"unknown group: {normalized_name}")
    save_role_groups(tuple(updated_groups), path)
    return updated


def link_role_group(parent: str, child: str, path: Path | None = None) -> None:
    """Link one child under one parent role group."""
    normalized_parent = parent.strip()
    normalized_child = child.strip()
    groups = load_role_groups(path)
    if not any(group.name == normalized_parent for group in groups):
        raise ValueError(f"unknown group: {normalized_parent}")
    if not any(group.name == normalized_child for group in groups):
        raise ValueError(f"unknown group: {normalized_child}")
    updated: list[RoleGroup] = []
    for group in groups:
        if group.name != normalized_parent:
            updated.append(group)
            continue
        if normalized_child in group.members:
            updated.append(group)
            continue
        updated.append(
            RoleGroup(
                name=group.name,
                description=group.description,
                members=group.members + (normalized_child,),
            )
        )
    save_role_groups(tuple(updated), path)


def unlink_role_group(parent: str, child: str, path: Path | None = None) -> None:
    """Remove one child link from one parent role group."""
    normalized_parent = parent.strip()
    normalized_child = child.strip()
    groups = load_role_groups(path)
    found_parent = False
    updated: list[RoleGroup] = []
    for group in groups:
        if group.name != normalized_parent:
            updated.append(group)
            continue
        found_parent = True
        updated.append(
            RoleGroup(
                name=group.name,
                description=group.description,
                members=tuple(item for item in group.members if item != normalized_child),
            )
        )
    if not found_parent:
        raise ValueError(f"unknown group: {normalized_parent}")
    save_role_groups(tuple(updated), path)


def assign_node_role_group(node_name: str, group_name: str, path: Path | None = None) -> Node:
    """Assign one node to one role group."""
    node = find_node(node_name, path)
    if node is None:
        raise ValueError(f"unknown node: {node_name}")
    if not any(group.name == group_name for group in load_role_groups(path)):
        raise ValueError(f"unknown group: {group_name}")
    if group_name in node.role_groups:
        return node
    updated = Node(
        name=node.name,
        parent=node.parent,
        kind=node.kind,
        runtime_role=node.runtime_role,
        address=node.address,
        ssh_user=node.ssh_user,
        description=node.description,
        runtime_hostname=node.runtime_hostname,
        node_id=node.node_id,
        platform=node.platform,
        client_port=node.client_port,
        open_ports=node.open_ports,
        services=node.services,
        exposed_endpoints=node.exposed_endpoints,
        service_records=node.service_records,
        role_groups=node.role_groups + (group_name,),
    )
    nodes = tuple(updated if item.name == node_name else item for item in load_nodes(path))
    save_nodes(nodes, path)
    return updated


def unassign_node_role_group(node_name: str, group_name: str, path: Path | None = None) -> Node:
    """Remove one role group assignment from one node."""
    node = find_node(node_name, path)
    if node is None:
        raise ValueError(f"unknown node: {node_name}")
    updated = Node(
        name=node.name,
        parent=node.parent,
        kind=node.kind,
        runtime_role=node.runtime_role,
        address=node.address,
        ssh_user=node.ssh_user,
        description=node.description,
        runtime_hostname=node.runtime_hostname,
        node_id=node.node_id,
        platform=node.platform,
        client_port=node.client_port,
        open_ports=node.open_ports,
        services=node.services,
        exposed_endpoints=node.exposed_endpoints,
        service_records=node.service_records,
        role_groups=tuple(item for item in node.role_groups if item != group_name),
    )
    nodes = tuple(updated if item.name == node_name else item for item in load_nodes(path))
    save_nodes(nodes, path)
    return updated
