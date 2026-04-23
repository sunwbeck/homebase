"""Typer entry point for the homebase CLI."""

from __future__ import annotations

import json
from pathlib import Path
import socket
import sys
from typing import Sequence

import typer
from rich.progress import BarColumn, Progress, TaskID, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table
from rich.console import Console

from homebase_cli.client import (
    DEFAULT_CLIENT_PORT,
    discovery_payload,
    local_profile,
    load_client_state,
    refresh_pair_code,
    serve_client,
)
from homebase_cli.docs_reader import docs_root, get_doc, list_docs
from homebase_cli.inventory import ansible_inventory_path, ansible_ping, open_ansible_inventory, write_ansible_inventory
from homebase_cli.output import print_docs_table, print_node_tree, print_resource_table, print_scan_table
from homebase_cli.packaging import (
    DEFAULT_REPO_URL,
    GitHubVersion,
    PackageOperationError,
    github_versions,
    install_command,
    install_github_ref,
    latest_github_version,
    load_install_state,
)
from homebase_cli.registry import (
    RoleGroup,
    add_node,
    add_role_group,
    assign_node_role_group,
    child_nodes,
    ensure_local_node,
    find_node,
    link_role_group,
    load_nodes,
    load_role_groups,
    rename_node,
    rename_role_group,
    remove_role_group,
    set_role_group_description,
    set_node_runtime_role,
    set_node_state,
    unassign_node_role_group,
    unlink_role_group,
    unset_node_state,
)
from homebase_cli.resources import all_resources, child_resources, find_resource
from homebase_cli.scanner import (
    detect_scannable_networks,
    fetch_package_status,
    request_package_install,
    request_package_upgrade,
    pair_with_client,
    save_discovered_nodes,
    scan_for_clients,
    DiscoveredNode,
    unregistered_discovered_nodes,
)
from homebase_cli.selftest import run_client_self_test
from homebase_cli.settings import (
    load_settings,
    runtime_roles,
    set_node_name,
    set_role,
)


app = typer.Typer(no_args_is_help=True, help="Manage homebase control and managed nodes.")
connect_app = typer.Typer(invoke_without_command=True, help="Discover and register managed nodes.")
node_app = typer.Typer(invoke_without_command=True, help="Inspect and manage registered nodes.")
group_app = typer.Typer(invoke_without_command=True, help="Inspect and manage groups.")
link_app = typer.Typer(invoke_without_command=True, help="Inspect and manage group-to-group links.")
role_app = typer.Typer(invoke_without_command=True, help="Show or change the local role: control or managed.")
ansible_app = typer.Typer(help="Run ansible-related helper commands.")
client_app = typer.Typer(help="Run the homebase client service on one managed node.")
inventory_app = typer.Typer(invoke_without_command=True, help="Show or open the ansible inventory YAML.")
state_app = typer.Typer(invoke_without_command=True, help="Store and inspect saved state values for registered nodes.")
package_app = typer.Typer(
    invoke_without_command=True,
    help="Check installed homebase revisions and install or update from GitHub.",
)
dev_app = typer.Typer(help="Development and internal commands.")
console = Console()
DEFAULT_KIND_CHOICES = ("control", "workstation", "host", "vm", "node")


def _show_group_help(ctx: typer.Context) -> None:
    """Show help instead of a missing-command error for command groups."""
    if ctx.invoked_subcommand is not None:
        return
    console.print(ctx.get_help())
    raise typer.Exit(code=0)


def _match_registered_nodes(scan_address: str, scan_node_id: str | None) -> str:
    """Return registered node names matching a discovered address or node id."""
    matches: list[str] = []
    for node in load_nodes():
        if node.address and node.address.strip() == scan_address:
            matches.append(node.name)
            continue
        if scan_node_id and node.node_id == scan_node_id:
            matches.append(node.name)
    return ", ".join(matches)


def _format_discovered_label(item: DiscoveredNode) -> str:
    return f"{item.address} | {item.discovery.hostname} | {item.discovery.platform}"


def _pick_from_list(label: str, options: Sequence[str]) -> str:
    if not options:
        raise typer.BadParameter(f"no options available for {label}")
    console.print(f"{label}:")
    for index, option in enumerate(options, start=1):
        console.print(f"{index}. {option}")
    choice = typer.prompt("Select number", type=int)
    if choice < 1 or choice > len(options):
        raise typer.BadParameter(f"invalid selection: {choice}")
    return options[choice - 1]


def _is_interactive() -> bool:
    """Return True when stdin is interactive."""
    return sys.stdin.isatty()


def _choose_github_version(repo_url: str, include_prerelease: bool = False) -> GitHubVersion:
    versions = github_versions(repo_url, include_prerelease=include_prerelease)
    if not versions:
        raise typer.BadParameter(f"no GitHub versions found for {repo_url}")
    selected = _pick_from_list("GitHub versions", [item.label for item in versions])
    return versions[[item.label for item in versions].index(selected)]


def _resolve_remote_package_target(resource: str):
    node = find_resource(resource)
    if node is None:
        raise typer.BadParameter(f"unknown resource: {resource}")
    if not node.address:
        raise typer.BadParameter(f"resource has no client address: {resource}")
    port = node.client_port or DEFAULT_CLIENT_PORT
    return node, port


def _choose_runtime_role() -> str:
    return _pick_from_list("Node type", list(runtime_roles()))


def _group_index() -> dict[str, RoleGroup]:
    return {group.name: group for group in load_role_groups()}


def _group_roots() -> list[str]:
    groups = load_role_groups()
    child_names = {member for group in groups for member in group.members}
    roots = [group.name for group in groups if group.name not in child_names]
    return sorted(roots)


def _resolve_local_node_name() -> str:
    node_name = _current_node_name()
    if not node_name:
        raise typer.BadParameter("local node name is not set; run `homebase init` first")
    return node_name


def _resolve_inventory_node_target(resource: str | None) -> str:
    return resource.strip() if resource is not None else _resolve_local_node_name()


def _count_actions(*values: object) -> int:
    return sum(1 for value in values if value not in (None, False, ""))


def _find_group(name: str) -> RoleGroup | None:
    normalized = name.strip()
    for group in load_role_groups():
        if group.name == normalized:
            return group
    return None


def _show_node_details(node_name: str) -> None:
    node = find_node(node_name)
    if node is None:
        raise typer.BadParameter(f"unknown node: {node_name}")
    console.print(f"[bold]Node: {node.name}[/bold]")
    console.print(f"type: {node.runtime_role}")
    console.print(f"groups: {', '.join(node.role_groups) if node.role_groups else 'none'}")
    if node.parent:
        console.print(f"parent: {node.parent}")
    console.print(f"kind: {node.kind}")


def _show_group_details(group_name: str) -> None:
    group = _find_group(group_name)
    if group is None:
        raise typer.BadParameter(f"unknown group: {group_name}")
    assigned_nodes = [node.name for node in load_nodes() if group.name in node.role_groups]
    console.print(f"[bold]Group: {group.name}[/bold]")
    console.print(f"members: {', '.join(group.members) if group.members else 'none'}")
    console.print(f"assigned nodes: {', '.join(assigned_nodes) if assigned_nodes else 'none'}")
    console.print(f"description: {group.description or 'none'}")


def _print_registered_overview() -> None:
    nodes = load_nodes()
    groups = load_role_groups()
    console.print("[bold]Registered nodes[/bold]")
    table = Table(show_header=True, header_style="bold")
    table.add_column("Node")
    table.add_column("Type")
    table.add_column("Kind")
    table.add_column("Address")
    table.add_column("Groups")
    current_name = _current_node_name()
    if not nodes:
        if current_name and _current_runtime_role():
            table.add_row(f"{current_name} (local)", _current_runtime_role() or "", "control" if (_current_runtime_role() == "control") else "node", "", "")
            console.print(table)
        else:
            console.print("registered nodes: none")
    else:
        for node in nodes:
            label = f"{node.name} (local)" if current_name and node.name == current_name else node.name
            table.add_row(
                label,
                node.runtime_role,
                node.kind,
                node.address or "",
                ", ".join(node.role_groups) if node.role_groups else "",
            )
        console.print(table)

    console.print("\n[bold]Defined groups[/bold]")
    if not groups:
        console.print("defined groups: none")
        return
    group_table = Table(show_header=True, header_style="bold")
    group_table.add_column("Group")
    group_table.add_column("Members")
    group_table.add_column("Description")
    for group in groups:
        group_table.add_row(group.name, ", ".join(group.members) if group.members else "", group.description)
    console.print(group_table)


def _print_local_role() -> None:
    settings = load_settings()
    console.print(f"role: {settings.role or 'not set'}")
    console.print(f"node: {settings.node_name or 'not set'}")


def _set_local_role(runtime_role: str) -> None:
    try:
        updated = set_role(runtime_role)
        local_name = _current_node_name()
        if local_name:
            ensure_local_node(local_name, updated.role or "managed", runtime_hostname=socket.gethostname())
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"[green]Set local node type to {updated.role}[/green]")


def _render_group_tree(name: str, index: dict[str, object], rows: list[tuple[str, str]], depth: int = 0, seen: set[str] | None = None) -> None:
    if seen is None:
        seen = set()
    if name in seen:
        rows.append((f'{"  " * depth}{name}', "cycle"))
        return
    seen.add(name)
    group = index.get(name)
    if group is None:
        rows.append((f'{"  " * depth}{name}', "missing"))
        return
    rows.append((f'{"  " * depth}{group.name}', "group"))
    for member in group.members:
        _render_group_tree(member, index, rows, depth + 1, seen.copy())


@inventory_app.callback()
def inventory_callback(ctx: typer.Context) -> None:
    """Show the ansible inventory YAML when inventory is called directly."""
    if ctx.invoked_subcommand is not None:
        return
    _require_role("control")
    target = write_ansible_inventory()
    console.print(f"[green]Inventory YAML:[/green] {target}")
    console.print(target.read_text(encoding="utf-8"))
    raise typer.Exit(code=0)


@connect_app.callback()
def connect_callback(ctx: typer.Context) -> None:
    """Show help when connect is called without a subcommand."""
    _show_group_help(ctx)


@role_app.callback()
def role_callback(ctx: typer.Context) -> None:
    """Show help when role is called without a subcommand."""
    _show_group_help(ctx)


@node_app.callback()
def node_callback(ctx: typer.Context) -> None:
    """Show help when node is called without a subcommand."""
    _show_group_help(ctx)


@group_app.callback()
def group_callback(ctx: typer.Context) -> None:
    """Show help when group is called without a subcommand."""
    _show_group_help(ctx)


@link_app.callback()
def link_callback(ctx: typer.Context) -> None:
    """Show help when link is called without a subcommand."""
    _show_group_help(ctx)


@state_app.callback()
def state_callback(ctx: typer.Context) -> None:
    """Show standard help when state is called without a subcommand."""
    if ctx.invoked_subcommand is not None:
        return
    console.print(ctx.get_help())
    raise typer.Exit(code=0)


@package_app.callback()
def package_callback(ctx: typer.Context) -> None:
    """Show standard help when package is called without a subcommand."""
    if ctx.invoked_subcommand is not None:
        return
    console.print(ctx.get_help())
    raise typer.Exit(code=0)


def _require_role(*allowed: str) -> None:
    current = load_settings().role
    if current is None:
        return
    if current not in allowed:
        joined = ", ".join(allowed)
        raise typer.BadParameter(f"this command is for role {joined}; current role is {current}")


def _current_runtime_role() -> str | None:
    return load_settings().role


def _current_node_name() -> str | None:
    return load_settings().node_name


def _choose_parent() -> str | None:
    mode = _pick_from_list("Hierarchy", ["Top-level node", "Child of an existing node"])
    if mode.startswith("Top-level"):
        return None
    parents = [node.name for node in load_nodes()]
    if not parents:
        raise typer.BadParameter("no existing nodes available to attach a child under")
    return _pick_from_list("Parent node", parents)


def _choose_kind() -> str:
    options = [*DEFAULT_KIND_CHOICES, "custom"]
    selected = _pick_from_list("Node kind", list(options))
    if selected == "custom":
        custom_kind = typer.prompt("Custom kind").strip()
        if not custom_kind:
            raise typer.BadParameter("custom kind cannot be empty")
        return custom_kind
    return selected


def _choose_discovered_node() -> DiscoveredNode:
    pending = list(unregistered_discovered_nodes())
    if not pending:
        raise typer.BadParameter("no unregistered discovered nodes found; run `homebase node scan` first")
    labels = [_format_discovered_label(item) for item in pending]
    selected_label = _pick_from_list("Discovered nodes", labels)
    return pending[labels.index(selected_label)]


def _resolve_profile_for_node(selected: DiscoveredNode, client_port: int) -> object:
    pair_code = typer.prompt("8-digit pairing code", type=str).strip()
    if len(pair_code) != 8 or not pair_code.isdigit():
        raise typer.BadParameter("pairing code must be exactly 8 digits")
    profile = pair_with_client(selected.address, pair_code, port=client_port)
    if profile is None:
        raise typer.BadParameter("pairing failed; verify the code shown on the client and try again")
    return profile


@connect_app.command("scan")
def node_scan_command(
    cidr: str | None = typer.Argument(None, help="Optional CIDR to scan, such as 192.168.219.0/24."),
    port: int = typer.Option(DEFAULT_CLIENT_PORT, "--port", help="TCP port exposed by the homebase client."),
    timeout: float = typer.Option(0.35, "--timeout", help="Per-host timeout in seconds."),
) -> None:
    """Scan a local network for homebase clients and update the discovery cache."""
    _require_role("control")
    networks = (cidr,) if cidr is not None else detect_scannable_networks()
    if not networks:
        console.print("[yellow]No scannable IPv4 networks detected.[/yellow]")
        return

    discovered = []
    rows: list[tuple[str, str, str, str, str, str]] = []
    for network in networks:
        results = scan_for_clients(network, port=port, timeout=timeout)
        discovered.extend(results)
        for item in results:
            rows.append(
                (
                    item.address,
                    _match_registered_nodes(item.address, item.discovery.node_id),
                    item.discovery.hostname,
                    item.discovery.node_id,
                    item.discovery.platform,
                    item.discovery.version,
                )
            )
    save_discovered_nodes(tuple(discovered))
    if not rows:
        console.print("[yellow]No homebase clients found. Discovery cache updated with 0 entries.[/yellow]")
        return
    print_scan_table(rows)
    console.print(f"[green]Updated discovery cache with {len(discovered)} client(s).[/green]")


@connect_app.command("add")
def node_add_command(
    name: str | None = typer.Argument(None, help="Canonical node name such as host.app."),
    parent: str | None = typer.Option(None, "--parent", help="Optional parent node such as host."),
    kind: str | None = typer.Option(None, "--kind", help="Node kind such as control, workstation, host, vm, or node."),
    ssh_user: str | None = typer.Option(None, "--ssh-user", help="SSH user for the node."),
    description: str = typer.Option("", "--description", help="Short human-readable description."),
    client_port: int = typer.Option(DEFAULT_CLIENT_PORT, "--client-port", help="TCP port exposed by the homebase client."),
) -> None:
    """Add one node to the persistent local registry, preferably from discovered clients."""
    _require_role("control")
    selected = _choose_discovered_node()
    profile = _resolve_profile_for_node(selected, client_port)
    resolved_name = name or typer.prompt("Node name", default=selected.discovery.hostname)
    resolved_parent = parent if parent is not None else _choose_parent()
    resolved_kind = kind if kind is not None else _choose_kind()
    resolved_ssh_user = ssh_user if ssh_user is not None else typer.prompt("SSH user", default="", show_default=False).strip() or None
    resolved_description = description or typer.prompt("Description", default="", show_default=False).strip()
    try:
        node = add_node(
            name=resolved_name,
            parent=resolved_parent,
            kind=resolved_kind,
            address=selected.address,
            ssh_user=resolved_ssh_user,
            description=resolved_description,
            runtime_hostname=profile.hostname,
            node_id=profile.node_id,
            platform=profile.platform,
            client_port=client_port,
            open_ports=profile.open_ports,
            services=profile.services,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"[green]Registered node {node.name}[/green]")


@app.command("status")
def status_command() -> None:
    """Show the current registered nodes, groups, and basic state."""
    _require_role("control")
    _print_registered_overview()


@node_app.command("list")
def node_list_command(resource: str | None = typer.Argument(None, help="Optional resource path such as host.")) -> None:
    """List all registered nodes, or child nodes under one parent."""
    _require_role("control")
    if resource is None:
        resources = load_nodes()
    else:
        try:
            resources = child_resources(resource)
        except KeyError as exc:
            raise typer.BadParameter(str(exc)) from exc

    rows = []
    current_name = _current_node_name()
    for item in resources:
        label = f"{item.name} (local)" if current_name and item.name == current_name else item.name
        rows.append((label, item.kind, item.address or ""))
    if not rows:
        if resource is None:
            console.print("[yellow]No registered nodes.[/yellow]")
        else:
            console.print(f"[yellow]No children under {resource}.[/yellow]")
        return
    print_resource_table(rows)


@node_app.command("show")
def node_show_command(resource: str = typer.Argument(..., help="Canonical node name.")) -> None:
    """Show detailed information for one registered node."""
    _require_role("control")
    item = find_resource(resource)
    if item is None:
        raise typer.BadParameter(f"unknown resource: {resource}")

    console.print(f"[bold]{item.name}[/bold]")
    console.print(f"kind: {item.kind}")
    if item.parent:
        console.print(f"parent: {item.parent}")
    if item.address:
        console.print(f"address: {item.address}")
    if item.runtime_hostname:
        console.print(f"runtime hostname: {item.runtime_hostname}")
    if item.node_id:
        console.print(f"node id: {item.node_id}")
    if item.platform:
        console.print(f"platform: {item.platform}")
    if item.client_port is not None:
        console.print(f"client port: {item.client_port}")
    if item.open_ports:
        console.print(f"open ports: {', '.join(str(port) for port in item.open_ports)}")
    if item.services:
        console.print(f"services: {', '.join(item.services)}")
    if item.ssh_user:
        console.print(f"ssh user: {item.ssh_user}")
    if item.description:
        console.print(f"description: {item.description}")
    children = child_nodes(item.name)
    if children:
        console.print("children:")
        for child in children:
            console.print(f"- {child.name}")


@ansible_app.command("inventory")
def ansible_inventory_command(
    output: Path | None = typer.Option(None, "--output", help="Optional output path for the rendered inventory."),
) -> None:
    """Render the current node registry as an ansible YAML inventory."""
    _require_role("control")
    if output is None:
        output = Path("inventory.yml")
    write_ansible_inventory(output)
    console.print(f"[green]Wrote ansible inventory to {output}[/green]")


@ansible_app.command("ping")
def ansible_ping_command(node: str = typer.Argument(..., help="Registered node name.")) -> None:
    """Run ansible ping against one registered node."""
    _require_role("control")
    result = ansible_ping(node)
    if result.stdout:
        console.print(result.stdout)
    if result.stderr:
        console.print(result.stderr)
    if result.returncode != 0:
        raise typer.Exit(code=result.returncode)


@app.command("docs")
def docs_command(doc: str | None = typer.Argument(None, help="Optional docs key such as current-state.")) -> None:
    """List known docs or show one doc path."""
    if doc is None:
        rows = [(entry.key, entry.filename, entry.summary) for entry in list_docs()]
        print_docs_table(rows)
        console.print(f"\nDocs root: [cyan]{docs_root()}[/cyan]")
        return

    entry = get_doc(doc)
    if entry is None:
        raise typer.BadParameter(f"unknown doc: {doc}")

    console.print(f"[bold]{entry.title}[/bold]")
    console.print(f"path: {entry.path}")
    console.print(f"summary: {entry.summary}")


@client_app.command("identity")
def client_identity_command() -> None:
    """Print the local homebase client discovery payload as JSON."""
    _require_role("managed")
    console.print(json.dumps(discovery_payload(), indent=2, sort_keys=True))


@client_app.command("code")
def client_code_command(
    refresh: bool = typer.Option(False, "--refresh", help="Generate a new 8-digit pairing code before printing it."),
) -> None:
    """Print the current local pairing code."""
    _require_role("managed")
    state = refresh_pair_code() if refresh else load_client_state()
    console.print(state.pair_code)


@client_app.command("profile")
def client_profile_command() -> None:
    """Print the local paired profile only for local inspection."""
    _require_role("managed")
    profile = local_profile()
    console.print(json.dumps(
        {
            "node_id": profile.node_id,
            "hostname": profile.hostname,
            "platform": profile.platform,
            "version": profile.version,
            "open_ports": list(profile.open_ports),
            "services": list(profile.services),
        },
        indent=2,
        sort_keys=True,
    ))


@client_app.command("serve")
def client_serve_command(
    host: str = typer.Option("0.0.0.0", "--host", help="Listen address for the client endpoint."),
    port: int = typer.Option(DEFAULT_CLIENT_PORT, "--port", help="Listen port for the client endpoint."),
) -> None:
    """Serve the local homebase client identity over HTTP."""
    _require_role("managed")
    console.print(f"serving homebase client on {host}:{port}")
    serve_client(host=host, port=port)


@app.command("init")
def init_command(
    role: str | None = typer.Option(None, "--role", help="Optional node type to set directly: control or managed."),
    name: str | None = typer.Option(None, "--name", help="Optional local node name to register directly."),
) -> None:
    """Initialize this installation as a control node or managed node."""
    selected = role.strip().lower() if role is not None else _choose_runtime_role()
    selected_name = (name.strip() if name is not None else "") or typer.prompt("Node name", default=_current_node_name() or socket.gethostname())
    try:
        updated = set_role(selected)
        previous_name = _current_node_name()
        set_node_name(selected_name)
        local_node = ensure_local_node(
            selected_name,
            updated.role or "managed",
            runtime_hostname=socket.gethostname(),
            previous_name=previous_name,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"[green]Set local node type to {updated.role}[/green]")
    console.print(f"[green]Registered local node name:[/green] {local_node.name}")


@app.command("list", hidden=True)
def inventory_list_command(target: str | None = typer.Argument(None, help="Optional node or group name.")) -> None:
    """List registered nodes and groups, or show one node or group."""
    _require_role("control")
    groups = load_role_groups()
    nodes = load_nodes()
    if target is not None:
        node = find_node(target)
        if node is not None:
            console.print(f"[bold]Node: {node.name}[/bold]")
            console.print(f"type: {node.runtime_role}")
            console.print(f"groups: {', '.join(node.role_groups) if node.role_groups else 'none'}")
            if node.parent:
                console.print(f"parent: {node.parent}")
            console.print(f"kind: {node.kind}")
            return
        group = next((item for item in groups if item.name == target.strip()), None)
        if group is None:
            raise typer.BadParameter(f"unknown node or group: {target}")
        assigned_nodes = [node.name for node in nodes if group.name in node.role_groups]
        console.print(f"[bold]Group: {group.name}[/bold]")
        console.print(f"members: {', '.join(group.members) if group.members else 'none'}")
        console.print(f"assigned nodes: {', '.join(assigned_nodes) if assigned_nodes else 'none'}")
        console.print(f"description: {group.description or 'none'}")
        return

    console.print("[bold]Registered nodes[/bold]")
    if not nodes:
        current_name = _current_node_name()
        current_role = _current_runtime_role()
        if current_name and current_role:
            table = Table(show_header=True, header_style="bold")
            table.add_column("Node")
            table.add_column("Type")
            table.add_column("Groups")
            table.add_row(f"{current_name} (local)", current_role, "")
            console.print(table)
        else:
            console.print("registered nodes: none")
    else:
        table = Table(show_header=True, header_style="bold")
        table.add_column("Node")
        table.add_column("Type")
        table.add_column("Groups")
        current_name = _current_node_name()
        for node in nodes:
            label = f"{node.name} (local)" if current_name and node.name == current_name else node.name
            table.add_row(label, node.runtime_role, ", ".join(node.role_groups) if node.role_groups else "")
        console.print(table)

    console.print("\n[bold]Defined groups[/bold]")
    if not groups:
        console.print("defined groups: none")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("Group")
    table.add_column("Members")
    table.add_column("Description")
    for group in groups:
        table.add_row(group.name, ", ".join(group.members) if group.members else "", group.description)
    console.print(table)


@role_app.command("show")
def role_show_command() -> None:
    """Show the local role and local node name."""
    _print_local_role()


@role_app.command("edit")
def role_edit_command(
    runtime_role: str = typer.Argument(..., help="New local role: control or managed."),
) -> None:
    """Edit the local role."""
    _set_local_role(runtime_role)


@inventory_app.command("name", hidden=True)
def inventory_name_command(
    target: str | None = typer.Argument(None, help="Optional node name. Defaults to the local node."),
    edit: str | None = typer.Option(None, "--edit", help="New node name."),
) -> None:
    """Show or edit one registered node name."""
    _require_role("control")
    resource = _resolve_inventory_node_target(target)
    node = find_node(resource)
    if node is None:
        raise typer.BadParameter(f"unknown node: {resource}")
    if edit is None:
        console.print(node.name)
        return
    try:
        renamed = rename_node(node.name, edit)
        if _current_node_name() == node.name:
            set_node_name(renamed.name)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"[green]Renamed node:[/green] {node.name} -> {renamed.name}")


@node_app.command("edit")
def node_edit_command(
    target: str = typer.Argument(..., help="Current node name."),
    field: str = typer.Argument(..., help="Field to edit: name or role."),
    value: str = typer.Argument(..., help="New value."),
) -> None:
    """Edit one registered node."""
    normalized = field.strip().lower()
    if normalized == "name":
        inventory_name_command(target, edit=value)
        return
    if normalized == "role":
        try:
            updated = set_node_runtime_role(target, value)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        console.print(f"[green]Set node type:[/green] {updated.name} -> {updated.runtime_role}")
        return
    raise typer.BadParameter("node edit field must be one of: name, role")


@group_app.command("list")
def group_list_command(target: str | None = typer.Argument(None, help="Optional group name.")) -> None:
    """List groups, or show one group."""
    _require_role("control")
    if target is None:
        groups = load_role_groups()
        if not groups:
            console.print("defined groups: none")
            return
        table = Table(show_header=True, header_style="bold")
        table.add_column("Group")
        table.add_column("Members")
        table.add_column("Description")
        for group in groups:
            table.add_row(group.name, ", ".join(group.members) if group.members else "", group.description)
        console.print(table)
        return
    _show_group_details(target)


@group_app.command("show")
def group_show_command(group: str = typer.Argument(..., help="Group name.")) -> None:
    """Show one group."""
    _require_role("control")
    _show_group_details(group)


@group_app.command("add")
def group_add_command(group: str = typer.Argument(..., help="New group name.")) -> None:
    """Add one group."""
    _require_role("control")
    try:
        created = add_role_group(name=group, description="")
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"[green]Added group:[/green] {created.name}")


@group_app.command("edit")
def group_edit_command(
    group: str = typer.Argument(..., help="Current group name."),
    field: str = typer.Argument(..., help="Field to edit: name or description."),
    value: str = typer.Argument(..., help="New value."),
) -> None:
    """Edit one group."""
    _require_role("control")
    normalized = field.strip().lower()
    try:
        if normalized == "name":
            updated = rename_role_group(group, value)
            console.print(f"[green]Renamed group:[/green] {group} -> {updated.name}")
            return
        if normalized == "description":
            updated = set_role_group_description(group, value)
            console.print(f"[green]Updated group description:[/green] {updated.name}")
            return
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    raise typer.BadParameter("group edit field must be one of: name, description")


@group_app.command("remove")
def group_remove_command(group: str = typer.Argument(..., help="Group name.")) -> None:
    """Remove one group."""
    _require_role("control")
    try:
        remove_role_group(group)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"[green]Removed group:[/green] {group.strip().lower()}")


@inventory_app.command("link", hidden=True)
def inventory_link_command(
    parent: str = typer.Argument(..., help="Parent group name."),
    child: str = typer.Argument(..., help="Child group name."),
    add: bool = typer.Option(False, "--add", help="Add the link."),
    remove: bool = typer.Option(False, "--remove", help="Remove the link."),
) -> None:
    """Add or remove one group-to-group link."""
    _require_role("control")
    if add == remove:
        raise typer.BadParameter("choose exactly one action: --add or --remove")
    try:
        if add:
            link_role_group(parent, child)
            console.print(f"[green]Linked group:[/green] {child.strip().lower()} -> {parent.strip().lower()}")
        else:
            unlink_role_group(parent, child)
            console.print(f"[green]Unlinked group:[/green] {child.strip().lower()} from {parent.strip().lower()}")
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


@link_app.command("list")
def link_list_command(group: str | None = typer.Argument(None, help="Optional parent group name.")) -> None:
    """List group-to-group links."""
    _require_role("control")
    groups = load_role_groups()
    table = Table(show_header=True, header_style="bold")
    table.add_column("Parent")
    table.add_column("Child")
    rows = []
    for item in groups:
        for member in item.members:
            if group is None or item.name == group:
                rows.append((item.name, member))
    if not rows:
        console.print("group links: none")
        return
    for parent, child in rows:
        table.add_row(parent, child)
    console.print(table)


@link_app.command("add")
def link_add_command(
    parent: str = typer.Argument(..., help="Parent group."),
    child: str = typer.Argument(..., help="Child group."),
) -> None:
    """Add one group-to-group link."""
    inventory_link_command(parent, child, add=True, remove=False)


@link_app.command("remove")
def link_remove_command(
    parent: str = typer.Argument(..., help="Parent group."),
    child: str = typer.Argument(..., help="Child group."),
) -> None:
    """Remove one group-to-group link."""
    inventory_link_command(parent, child, add=False, remove=True)


@inventory_app.command("assign", hidden=True)
def inventory_assign_command(
    resource: str = typer.Argument(..., help="Resource path such as host.app."),
    group: str = typer.Argument(..., help="Group name."),
    add: bool = typer.Option(False, "--add", help="Add the assignment."),
    remove: bool = typer.Option(False, "--remove", help="Remove the assignment."),
) -> None:
    """Add or remove one node-to-group assignment."""
    _require_role("control")
    if add == remove:
        raise typer.BadParameter("choose exactly one action: --add or --remove")
    try:
        if add:
            assign_node_role_group(resource, group)
            console.print(f"[green]Assigned node:[/green] {resource} -> {group.strip().lower()}")
        else:
            unassign_node_role_group(resource, group)
            console.print(f"[green]Removed node assignment:[/green] {resource} from {group.strip().lower()}")
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


@node_app.command("assign")
def node_assign_command(
    node: str = typer.Argument(..., help="Node name."),
    group: str = typer.Argument(..., help="Group name."),
) -> None:
    """Assign one node to one group."""
    inventory_assign_command(node, group, add=True, remove=False)


@node_app.command("unassign")
def node_unassign_command(
    node: str = typer.Argument(..., help="Node name."),
    group: str = typer.Argument(..., help="Group name."),
) -> None:
    """Remove one node from one group."""
    inventory_assign_command(node, group, add=False, remove=True)


@inventory_app.command("show")
def inventory_show_command() -> None:
    """Show the ansible inventory YAML."""
    _require_role("control")
    target = write_ansible_inventory()
    console.print(f"[green]Inventory YAML:[/green] {target}")
    console.print(target.read_text(encoding="utf-8"))


@inventory_app.command("edit")
def inventory_edit_command() -> None:
    """Open the ansible inventory YAML in the configured editor."""
    _require_role("control")
    target = open_ansible_inventory()
    console.print(f"[green]Opened ansible inventory:[/green] {target}")


@state_app.command("show")
def state_show_command(resource: str = typer.Argument(..., help="Resource path such as host.app.")) -> None:
    """Show saved state values for one registered node."""
    _require_role("control")
    node = find_node(resource)
    if node is None:
        raise typer.BadParameter(f"unknown node: {resource}")
    states = node.states
    if not states:
        console.print("[yellow]No state values saved yet.[/yellow]")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("Key")
    table.add_column("Value")
    for key, value in states:
        table.add_row(key, value)
    console.print(table)


@state_app.command("set")
def state_set_command(
    resource: str = typer.Argument(..., help="Resource path such as host.app."),
    key: str = typer.Argument(..., help="State key."),
    value: str = typer.Argument(..., help="State value."),
) -> None:
    """Set one saved state value on one registered node."""
    _require_role("control")
    try:
        set_node_state(resource, key, value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"[green]Set state:[/green] {resource} {key}={value}")


@state_app.command("unset")
def state_unset_command(
    resource: str = typer.Argument(..., help="Resource path such as host.app."),
    key: str = typer.Argument(..., help="State key."),
) -> None:
    """Remove one saved state value from one registered node."""
    _require_role("control")
    try:
        unset_node_state(resource, key)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"[green]Removed state:[/green] {resource} {key}")


@package_app.command("status")
def package_status_command(
    resource: str | None = typer.Argument(None, help="Optional resource path."),
) -> None:
    """Show the currently installed homebase revision on this node or one managed node."""
    if resource is not None:
        _require_role("control")
        node, port = _resolve_remote_package_target(resource)
        payload = fetch_package_status(node.address, port=port)
        if payload is None:
            console.print(f"[red]Package status failed.[/red] No response from {resource} at {node.address}:{port}")
            raise typer.Exit(code=1)
        console.print(f"[bold]Remote package status: {resource}[/bold]")
        console.print(f"address: {node.address}:{port}")
        console.print(f"installed version: {payload.get('installed_version') or 'not installed'}")
        if payload.get("requested_ref"):
            console.print(f"requested ref: {payload.get('requested_ref')}")
        if payload.get("resolved_ref"):
            console.print(f"resolved commit: {payload.get('resolved_ref')}")
        if payload.get("summary"):
            console.print(f"summary: {payload.get('summary')}")
        if payload.get("installed_at"):
            console.print(f"installed at: {payload.get('installed_at')}")
        return
    current = load_install_state()
    latest: GitHubVersion | None = None
    latest_error: str | None = None
    try:
        latest = latest_github_version(DEFAULT_REPO_URL)
    except RuntimeError as exc:
        latest_error = str(exc)
    console.print("[bold]Current package status[/bold]")
    console.print(f"installed version: {current.installed_version or 'not installed'}")
    console.print(f"repo: {current.repo_url or DEFAULT_REPO_URL}")
    if current.requested_ref:
        console.print(f"requested ref: {current.requested_ref}")
    if current.resolved_ref:
        console.print(f"resolved commit: {current.resolved_ref}")
    if current.summary:
        console.print(f"summary: {current.summary}")
    if current.installed_at:
        console.print(f"installed at: {current.installed_at}")
    if latest is not None:
        console.print(f"latest available: {latest.version}")
        console.print(f"latest summary: {latest.summary}")
    elif latest_error is not None:
        console.print(f"[yellow]latest lookup failed:[/yellow] {latest_error}")


@package_app.command("versions")
def package_versions_command(
    repo_url: str = typer.Option(DEFAULT_REPO_URL, "--repo", help="GitHub repository URL."),
    include_prerelease: bool = typer.Option(False, "--pre-release", help="Include prerelease GitHub releases."),
) -> None:
    """List installable GitHub refs with short release notes."""
    try:
        versions = github_versions(repo_url, include_prerelease=include_prerelease)
    except RuntimeError as exc:
        console.print(f"[red]Version lookup failed.[/red] {exc}")
        raise typer.Exit(code=1)
    if not versions:
        console.print(f"[yellow]No GitHub versions found for {repo_url}[/yellow]")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("#", justify="right")
    table.add_column("Ref")
    table.add_column("Kind")
    table.add_column("Published")
    table.add_column("Summary")
    for index, item in enumerate(versions, start=1):
        table.add_row(
            str(index),
            item.version,
            item.source,
            item.published_at or "",
            item.summary,
        )
    console.print(table)


@package_app.command("version", hidden=True)
def package_version_command(
    repo_url: str = typer.Option(DEFAULT_REPO_URL, "--repo", help="GitHub repository URL."),
    include_prerelease: bool = typer.Option(False, "--pre-release", help="Include prerelease GitHub releases."),
) -> None:
    """Hidden singular alias for `homebase package versions`."""
    package_versions_command(repo_url=repo_url, include_prerelease=include_prerelease)


def _run_install_flow(
    *,
    ref: str,
    repo_url: str,
    python_bin: str | None,
    summary: str | None,
) -> None:
    with Progress(
        SpinnerColumn(),
        TextColumn("{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(f"1/3 Resolve GitHub target: {ref}", total=100)
        progress.update(task, completed=100)
        task = progress.add_task(f"2/3 Install from GitHub ref: {ref}", total=100)
        tick_value = 0

        def install_tick() -> None:
            nonlocal tick_value
            if tick_value < 92:
                tick_value += 1
            progress.update(task, completed=tick_value)

        try:
            _, status = install_github_ref(
                ref,
                repo_url=repo_url,
                python_bin=python_bin,
                summary=summary,
                on_tick=install_tick,
            )
        except PackageOperationError as exc:
            progress.stop()
            console.print(f"[red]Package install failed.[/red] Log: {exc.log_path}")
            console.print(f"Check the log with: `less {exc.log_path}`")
            raise typer.Exit(code=1)
        progress.update(task, completed=100)
        task = progress.add_task("3/3 Record installed revision", total=100)
        progress.update(task, completed=100)
    console.print(f"[green]Installed version:[/green] {status.installed_version or 'unknown'}")
    console.print(f"[green]Requested ref:[/green] {status.requested_ref}")
    if status.resolved_ref:
        console.print(f"[green]Resolved commit:[/green] {status.resolved_ref}")
    if python_bin is not None:
        console.print(f"[green]Installed into Python:[/green] {python_bin}")


@package_app.command("install")
def package_install_command(
    resource: str | None = typer.Argument(None, help="Optional resource path."),
    ref: str | None = typer.Option(None, "--ref", help="GitHub ref to install: branch, tag, release tag, or commit SHA."),
    repo_url: str = typer.Option(DEFAULT_REPO_URL, "--repo", help="GitHub repository URL."),
    python_bin: str | None = typer.Option(None, "--python", help="Explicit Python executable to install into. Defaults to the current Python environment."),
    include_prerelease: bool = typer.Option(False, "--pre-release", help="Include prerelease GitHub releases when choosing interactively."),
) -> None:
    """Install one GitHub ref, or choose a version interactively."""
    selected_summary: str | None = None
    selected_ref = ref
    if selected_ref is None:
        try:
            chosen = _choose_github_version(repo_url, include_prerelease=include_prerelease)
        except RuntimeError as exc:
            console.print(f"[red]Version lookup failed.[/red] {exc}")
            raise typer.Exit(code=1)
        selected_ref = chosen.ref
        selected_summary = chosen.summary
    if resource is not None:
        _require_role("control")
        node, port = _resolve_remote_package_target(resource)
        with Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(f"1/2 Resolve remote target: {resource}", total=100)
            progress.update(task, completed=100)
            task = progress.add_task(f"2/2 Request install on {resource}", total=100)
            progress.update(task, completed=30)
            payload = request_package_install(
                node.address,
                ref=selected_ref,
                repo_url=repo_url,
                summary=selected_summary,
                port=port,
            )
            progress.update(task, completed=100)
        if payload is None:
            console.print(f"[red]Remote package install failed.[/red] No response from {resource} at {node.address}:{port}")
            raise typer.Exit(code=1)
        console.print(f"[green]Remote install completed:[/green] {resource}")
        console.print(f"installed version: {payload.get('installed_version') or 'unknown'}")
        console.print(f"requested ref: {payload.get('requested_ref') or selected_ref}")
        if payload.get("resolved_ref"):
            console.print(f"resolved commit: {payload.get('resolved_ref')}")
        return
    _run_install_flow(ref=selected_ref, repo_url=repo_url, python_bin=python_bin, summary=selected_summary)


@package_app.command("update")
def package_update_command(
    resource: str | None = typer.Argument(None, help="Optional resource path."),
    repo_url: str = typer.Option(DEFAULT_REPO_URL, "--repo", help="GitHub repository URL."),
    python_bin: str | None = typer.Option(None, "--python", help="Explicit Python executable to install into. Defaults to the current Python environment."),
    include_prerelease: bool = typer.Option(False, "--pre-release", help="Allow prerelease versions when selecting the latest target."),
) -> None:
    """Update to the latest GitHub release, or default branch when no release exists."""
    try:
        latest = latest_github_version(repo_url, include_prerelease=include_prerelease)
    except RuntimeError as exc:
        console.print(f"[red]Latest-version lookup failed.[/red] {exc}")
        raise typer.Exit(code=1)
    console.print(f"[bold]Selected latest target:[/bold] {latest.version}")
    console.print(f"summary: {latest.summary}")
    if resource is not None:
        _require_role("control")
        node, port = _resolve_remote_package_target(resource)
        with Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(f"1/2 Resolve remote target: {resource}", total=100)
            progress.update(task, completed=100)
            task = progress.add_task(f"2/2 Request update on {resource}", total=100)
            progress.update(task, completed=30)
            payload = request_package_upgrade(
                node.address,
                repo_url=repo_url,
                include_prerelease=include_prerelease,
                port=port,
            )
            progress.update(task, completed=100)
        if payload is None:
            console.print(f"[red]Remote package update failed.[/red] No response from {resource} at {node.address}:{port}")
            raise typer.Exit(code=1)
        console.print(f"[green]Remote update completed:[/green] {resource}")
        console.print(f"installed version: {payload.get('installed_version') or 'unknown'}")
        console.print(f"requested ref: {payload.get('requested_ref') or latest.ref}")
        if payload.get("resolved_ref"):
            console.print(f"resolved commit: {payload.get('resolved_ref')}")
        return
    _run_install_flow(ref=latest.ref, repo_url=repo_url, python_bin=python_bin, summary=latest.summary)


@dev_app.command("self-test")
def dev_self_test_command() -> None:
    """Run a local loopback self-test for discovery and pairing."""
    result = run_client_self_test()
    console.print("[green]local client self-test passed[/green]")
    console.print(f"node id: {result.node_id}")
    console.print(f"hostname: {result.hostname}")
    console.print(f"platform: {result.platform}")
    console.print(f"version: {result.version}")


def _build_client_app() -> typer.Typer:
    runtime_client_app = typer.Typer(help="Run the homebase client service on one managed node.")
    runtime_client_app.command("code")(client_code_command)
    runtime_client_app.command("serve")(client_serve_command)
    runtime_client_app.command("profile", hidden=True)(client_profile_command)
    runtime_client_app.command("identity", hidden=True)(client_identity_command)
    return runtime_client_app


def _build_node_app() -> typer.Typer:
    runtime_node_app = typer.Typer(invoke_without_command=True, help="Inspect and manage registered nodes.")
    runtime_node_app.callback()(node_callback)
    runtime_node_app.command("list")(node_list_command)
    runtime_node_app.command("show")(node_show_command)
    runtime_node_app.command("edit")(node_edit_command)
    runtime_node_app.command("assign")(node_assign_command)
    runtime_node_app.command("unassign")(node_unassign_command)
    return runtime_node_app


def _build_connect_app() -> typer.Typer:
    runtime_connect_app = typer.Typer(invoke_without_command=True, help="Discover and register managed nodes.")
    runtime_connect_app.callback()(connect_callback)
    runtime_connect_app.command("scan")(node_scan_command)
    runtime_connect_app.command("add")(node_add_command)
    return runtime_connect_app


def _build_group_app() -> typer.Typer:
    runtime_group_app = typer.Typer(invoke_without_command=True, help="Inspect and manage groups.")
    runtime_group_app.callback()(group_callback)
    runtime_group_app.command("list")(group_list_command)
    runtime_group_app.command("show")(group_show_command)
    runtime_group_app.command("add")(group_add_command)
    runtime_group_app.command("edit")(group_edit_command)
    runtime_group_app.command("remove")(group_remove_command)
    return runtime_group_app


def _build_link_app() -> typer.Typer:
    runtime_link_app = typer.Typer(invoke_without_command=True, help="Inspect and manage group-to-group links.")
    runtime_link_app.callback()(link_callback)
    runtime_link_app.command("list")(link_list_command)
    runtime_link_app.command("add")(link_add_command)
    runtime_link_app.command("remove")(link_remove_command)
    return runtime_link_app


def _build_role_app() -> typer.Typer:
    runtime_role_app = typer.Typer(invoke_without_command=True, help="Show or change the local role: control or managed.")
    runtime_role_app.callback()(role_callback)
    runtime_role_app.command("show")(role_show_command)
    runtime_role_app.command("edit")(role_edit_command)
    return runtime_role_app


def _build_package_app() -> typer.Typer:
    runtime_package_app = typer.Typer(
        invoke_without_command=True,
        help="Check installed homebase revisions and install or update from GitHub.",
    )
    runtime_package_app.callback()(package_callback)
    runtime_package_app.command("status")(package_status_command)
    runtime_package_app.command("versions")(package_versions_command)
    runtime_package_app.command("version", hidden=True)(package_version_command)
    runtime_package_app.command("update")(package_update_command)
    runtime_package_app.command("install")(package_install_command)
    return runtime_package_app


def _build_dev_app() -> typer.Typer:
    runtime_dev_app = typer.Typer(help="Development and internal commands.")
    runtime_dev_app.command("self-test")(dev_self_test_command)
    runtime_dev_app.command("docs")(docs_command)
    runtime_dev_app.add_typer(ansible_app, name="ansible")
    return runtime_dev_app


def _build_root_app() -> typer.Typer:
    runtime_app = typer.Typer(no_args_is_help=True, help="Manage homebase control and managed nodes.")
    runtime_app.command("init")(init_command)
    current_role = _current_runtime_role()
    if current_role in (None, "control"):
        runtime_app.command("status")(status_command)
        runtime_app.add_typer(_build_role_app(), name="role")
        runtime_app.add_typer(_build_node_app(), name="node")
        runtime_app.add_typer(_build_group_app(), name="group")
        runtime_app.add_typer(_build_link_app(), name="link")
        runtime_app.add_typer(inventory_app, name="inventory")
        runtime_app.add_typer(_build_connect_app(), name="connect")
        runtime_app.add_typer(state_app, name="state")
    if current_role in (None, "managed"):
        runtime_app.add_typer(_build_client_app(), name="client")
    runtime_app.add_typer(_build_package_app(), name="package")
    runtime_app.add_typer(_build_dev_app(), name="dev")
    return runtime_app


app = _build_root_app()


def main() -> None:
    """Run the CLI app."""
    app()


if __name__ == "__main__":
    main()
