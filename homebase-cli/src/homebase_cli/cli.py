"""Typer entry point for the homebase CLI."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import socket
import sys
import time
from typing import Sequence
from datetime import UTC, datetime
from textwrap import dedent

import typer
from rich.progress import BarColumn, Progress, TaskID, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table
from rich.console import Console

from homebase_cli.client import (
    ConnectRuntime,
    CONNECT_LOG_PATH,
    DEFAULT_CLIENT_PORT,
    detect_primary_address,
    connect_server_running,
    clear_connect_runtime,
    discovery_payload,
    local_profile,
    load_client_state,
    load_connect_runtime,
    refresh_pair_code,
    serve_client,
    save_connect_runtime,
    stop_connect_server,
)
from homebase_cli.docs_reader import docs_root, get_doc, list_docs
from homebase_cli.inventory import ansible_inventory_path, ansible_ping, open_ansible_inventory, write_ansible_inventory
from homebase_cli.output import print_docs_table, print_node_tree, print_scan_table
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
    load_discovered_nodes,
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


app = typer.Typer(
    no_args_is_help=True,
    help="Operate homebase nodes from either a controller or a managed node.",
)
connect_app = typer.Typer(
    invoke_without_command=True,
    help="Pair controllers and managed nodes.",
)
node_app = typer.Typer(
    invoke_without_command=True,
    help="Inspect and edit registered nodes.",
)
group_app = typer.Typer(
    invoke_without_command=True,
    help="Inspect and edit reusable node groups.",
)
link_app = typer.Typer(
    invoke_without_command=True,
    help="Manage parent-child links between groups.",
)
role_app = typer.Typer(
    invoke_without_command=True,
    help="Show or change local or registered node runtime roles.",
)
ansible_app = typer.Typer(help="Run ansible helper commands against the current inventory.")
inventory_app = typer.Typer(
    invoke_without_command=True,
    help="Work with the rendered ansible inventory file.",
)
state_app = typer.Typer(
    invoke_without_command=True,
    help="Save simple labels or operator state on registered nodes.",
)
package_app = typer.Typer(
    invoke_without_command=True,
    help="Check the installed homebase revision and install or update from GitHub.",
)
dev_app = typer.Typer(help="Development, diagnostics, and internal helper commands.")
service_app = typer.Typer(
    invoke_without_command=True,
    help="Run the local background service on this node.",
)
console = Console()
DEFAULT_KIND_CHOICES = ("controller", "workstation", "host", "vm", "node")


def _show_group_help(ctx: typer.Context) -> None:
    """Show help instead of a missing-command error for command groups."""
    if ctx.invoked_subcommand is not None:
        return
    console.print(ctx.get_help())
    raise typer.Exit(code=0)


def _state_summary(states: Sequence[tuple[str, str]]) -> str:
    """Render one compact saved-state summary."""
    if not states:
        return ""
    return ", ".join(f"{key}={value}" for key, value in states)


def _group_parents(group_name: str) -> list[str]:
    """Return the direct parent groups for one group."""
    return sorted(group.name for group in load_role_groups() if group_name in group.members)


def _assigned_group_nodes(group_name: str) -> list[str]:
    """Return the node names assigned to one group."""
    return sorted(node.name for node in load_nodes() if group_name in node.role_groups)


def _node_label(node_name: str) -> str:
    """Render one node label, marking the current node."""
    current_name = _current_node_name()
    if current_name and node_name == current_name:
        return f"{node_name} (local)"
    return node_name


def _node_service_state(node_name: str) -> str:
    """Return the local background-service state when known."""
    current_name = _current_node_name()
    if current_name and current_name == node_name:
        return "running" if connect_server_running() is not None else "stopped"
    return ""


def _inventory_nodes():
    """Return registered nodes, keeping the local node visible."""
    nodes = list(load_nodes())
    current_name = _current_node_name()
    current_role = _current_runtime_role()
    if current_name and current_role and not any(node.name == current_name for node in nodes):
        try:
            profile = local_profile()
            nodes.append(
                ensure_local_node(
                    current_name,
                    current_role,
                    runtime_hostname=profile.hostname,
                    address=detect_primary_address() or None,
                    platform=profile.platform,
                    open_ports=profile.open_ports,
                    services=profile.services,
                )
            )
        except Exception:
            nodes.append(ensure_local_node(current_name, current_role, runtime_hostname=socket.gethostname()))
    return tuple(sorted(nodes, key=lambda item: item.name))


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


def _format_pair_code(code: str) -> str:
    """Render one pairing code in easier-to-read blocks."""
    normalized = code.strip()
    if len(normalized) == 8 and normalized.isdigit():
        return f"{normalized[:4]} {normalized[4:]}"
    return normalized


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
    return _pick_from_list("Local runtime role", list(runtime_roles()))


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
    local_profile_data = None
    if _current_node_name() and node.name == _current_node_name():
        try:
            local_profile_data = local_profile()
        except Exception:
            local_profile_data = None
    platform_value = node.platform or (local_profile_data.platform if local_profile_data is not None else None) or ""
    open_ports = node.open_ports or (local_profile_data.open_ports if local_profile_data is not None else ())
    services = node.services or (local_profile_data.services if local_profile_data is not None else ())
    address_value = node.address or (detect_primary_address() if local_profile_data is not None else None) or ""
    console.print(f"[bold]Node: {_node_label(node.name)}[/bold]")

    identity = Table(show_header=False, box=None)
    identity.add_column("Field", style="bold")
    identity.add_column("Value")
    identity.add_row("role", node.runtime_role)
    identity.add_row("kind", node.kind)
    identity.add_row("parent", node.parent or "")
    identity.add_row("description", node.description or "")
    identity.add_row("node id", node.node_id or "")
    identity.add_row("ssh user", node.ssh_user or "")
    console.print("[bold]Identity[/bold]")
    console.print(identity)

    runtime = Table(show_header=False, box=None)
    runtime.add_column("Field", style="bold")
    runtime.add_column("Value")
    runtime.add_row("hostname", node.runtime_hostname or (local_profile_data.hostname if local_profile_data is not None else "") or "")
    runtime.add_row("os", platform_value)
    runtime.add_row("service", _node_service_state(node.name))
    runtime.add_row("groups", ", ".join(node.role_groups) if node.role_groups else "")
    runtime.add_row("states", _state_summary(node.states))
    console.print("[bold]Runtime[/bold]")
    console.print(runtime)

    network = Table(show_header=False, box=None)
    network.add_column("Field", style="bold")
    network.add_column("Value")
    network.add_row("address", address_value)
    network.add_row("connect port", str(node.client_port) if node.client_port is not None else "")
    network.add_row("open ports", ", ".join(str(port) for port in open_ports))
    network.add_row("services", ", ".join(services))
    console.print("[bold]Network[/bold]")
    console.print(network)

    children = child_nodes(node.name)
    relations = Table(show_header=False, box=None)
    relations.add_column("Field", style="bold")
    relations.add_column("Value")
    relations.add_row("children", ", ".join(child.name for child in children))
    console.print("[bold]Relations[/bold]")
    console.print(relations)


def _show_group_details(group_name: str) -> None:
    group = _find_group(group_name)
    if group is None:
        raise typer.BadParameter(f"unknown group: {group_name}")
    assigned_nodes = _assigned_group_nodes(group.name)
    console.print(f"[bold]Group: {group.name}[/bold]")
    table = Table(show_header=False, box=None)
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("description", group.description or "")
    table.add_row("parents", ", ".join(_group_parents(group.name)))
    table.add_row("children", ", ".join(group.members))
    table.add_row("assigned nodes", ", ".join(assigned_nodes))
    console.print(table)


def _print_registered_overview() -> None:
    nodes = _inventory_nodes()
    console.print("[bold]Node status[/bold]")
    table = Table(show_header=True, header_style="bold")
    table.add_column("Node")
    table.add_column("Role")
    table.add_column("Address")
    table.add_column("Hostname")
    table.add_column("OS")
    table.add_column("Service")
    table.add_column("Open Ports")
    table.add_column("Services")
    table.add_column("Groups")
    table.add_column("State")
    current_name = _current_node_name()
    if not nodes:
        console.print("registered nodes: none")
        return
    for node in nodes:
        local_profile_data = None
        if current_name and node.name == current_name:
            try:
                local_profile_data = local_profile()
            except Exception:
                local_profile_data = None
        table.add_row(
            _node_label(node.name),
            node.runtime_role,
            node.address or (detect_primary_address() if local_profile_data is not None else ""),
            node.runtime_hostname or (local_profile_data.hostname if local_profile_data is not None else ""),
            node.platform or (local_profile_data.platform if local_profile_data is not None else ""),
            _node_service_state(node.name),
            ", ".join(str(port) for port in (node.open_ports or (local_profile_data.open_ports if local_profile_data is not None else ()))),
            ", ".join(node.services or (local_profile_data.services if local_profile_data is not None else ())),
            ", ".join(node.role_groups) if node.role_groups else "",
            _state_summary(node.states),
        )
    console.print(table)


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
    """Show help when inventory is called without a subcommand."""
    if ctx.invoked_subcommand is not None:
        return
    _show_group_help(ctx)


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


@service_app.callback()
def service_callback(ctx: typer.Context) -> None:
    """Show standard help when service is called without a subcommand."""
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


def _needs_initialization() -> bool:
    """Return True when the local installation has not been initialized yet."""
    settings = load_settings()
    return not settings.role or not settings.node_name


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
        raise typer.BadParameter("no unregistered discovered nodes found; run `homebase connect scan` first")
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
    """Scan the local network for managed nodes that are running `homebase service start`.

    Run this on the controller before `homebase connect add`.
    """
    _require_role("controller")
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
    kind: str | None = typer.Option(None, "--kind", help="Node kind such as controller, workstation, host, vm, or node."),
    ssh_user: str | None = typer.Option(None, "--ssh-user", help="SSH user for the node."),
    description: str = typer.Option("", "--description", help="Short human-readable description."),
    client_port: int = typer.Option(DEFAULT_CLIENT_PORT, "--client-port", help="TCP port exposed by the homebase client."),
) -> None:
    """Pair one discovered managed node and register it in the local inventory.

    Typical flow:
      1. On the managed node, run `homebase connect code` and `homebase service start`
      2. On the controller, run `homebase connect scan`
      3. Then run this command and enter the 8-digit pairing code
    """
    _require_role("controller")
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


@connect_app.command("status")
def connect_status_command() -> None:
    """Show connection-related status for this node.

    Controller nodes see discovered managed nodes.
    Managed nodes see the current pair code, paired controllers, and service state.
    """
    current_role = _current_runtime_role()
    if current_role == "managed":
        state = load_client_state()
        runtime = connect_server_running()
        table = Table(show_header=True, header_style="bold")
        table.add_column("Pair Code")
        table.add_column("Controllers")
        table.add_column("Service")
        table.add_column("Endpoint")
        table.add_row(
            _format_pair_code(state.pair_code),
            ", ".join(state.paired_controllers) if state.paired_controllers else "",
            "running" if runtime is not None else "stopped",
            f"{runtime.host}:{runtime.port}" if runtime is not None else "",
        )
        console.print(table)
        return
    _require_role("controller")
    discovered = load_discovered_nodes()
    nodes = load_nodes()
    if not discovered:
        console.print("discovered nodes: none")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("Address")
    table.add_column("Hostname")
    table.add_column("OS")
    table.add_column("Version")
    table.add_column("Node")
    table.add_column("State")
    matched = {node.address: node.name for node in nodes if node.address}
    matched_ids = {node.node_id: node.name for node in nodes if node.node_id}
    for item in discovered:
        registered_name = matched.get(item.address) or matched_ids.get(item.discovery.node_id) or ""
        table.add_row(
            item.address,
            item.discovery.hostname,
            item.discovery.platform,
            item.discovery.version,
            registered_name,
            "registered" if registered_name else "discovered",
        )
    console.print(table)


@app.command("status")
def status_command() -> None:
    """Show one controller-side overview table for all registered nodes.

    This is the main fleet status view: node name, role, address, hostname,
    OS, service state, ports, services, groups, and saved state labels.
    """
    _require_role("controller")
    _print_registered_overview()


@node_app.command("list")
def node_list_command(resource: str | None = typer.Argument(None, help="Optional parent node name.")) -> None:
    """List registered nodes in one comparison table.

    Use `homebase node show <node>` when you want one node in detail.
    """
    _require_role("controller")
    if resource is None:
        resources = _inventory_nodes()
    else:
        try:
            resources = child_resources(resource)
        except KeyError as exc:
            raise typer.BadParameter(str(exc)) from exc
    if not resources:
        if resource is None:
            console.print("[yellow]No registered nodes.[/yellow]")
        else:
            console.print(f"[yellow]No children under {resource}.[/yellow]")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("Node")
    table.add_column("Role")
    table.add_column("Kind")
    table.add_column("Parent")
    table.add_column("Address")
    table.add_column("Hostname")
    table.add_column("Groups")
    table.add_column("Description")
    for item in resources:
        table.add_row(
            _node_label(item.name),
            item.runtime_role,
            item.kind,
            item.parent or "",
            item.address or "",
            item.runtime_hostname or "",
            ", ".join(item.role_groups) if item.role_groups else "",
            item.description,
        )
    console.print(table)


@node_app.command("show")
def node_show_command(resource: str = typer.Argument(..., help="Canonical node name.")) -> None:
    """Show one node in detail.

    The detailed view includes identity, runtime, network, hierarchy, groups,
    saved state labels, and direct children.
    """
    _require_role("controller")
    _show_node_details(resource)


@ansible_app.command("inventory")
def ansible_inventory_command(
    output: Path | None = typer.Option(None, "--output", help="Optional output path for the rendered inventory."),
) -> None:
    """Render the current controller inventory as ansible YAML."""
    _require_role("controller")
    if output is None:
        output = Path("inventory.yml")
    write_ansible_inventory(output)
    console.print(f"[green]Wrote ansible inventory to {output}[/green]")


@ansible_app.command("ping")
def ansible_ping_command(node: str = typer.Argument(..., help="Registered node name.")) -> None:
    """Run `ansible -m ping` against one registered node."""
    _require_role("controller")
    result = ansible_ping(node)
    if result.stdout:
        console.print(result.stdout)
    if result.stderr:
        console.print(result.stderr)
    if result.returncode != 0:
        raise typer.Exit(code=result.returncode)


@app.command("doc")
def docs_command(doc: str | None = typer.Argument(None, help="Optional docs key such as current-state.")) -> None:
    """List homebase docs or print one doc so it can be read in the terminal."""
    if doc is None:
        rows = [(entry.key, entry.filename, entry.summary) for entry in list_docs()]
        print_docs_table(rows)
        console.print("\nUse `homebase doc <key>` to print one document in the terminal.")
        local_root = docs_root()
        if local_root is not None:
            console.print(f"Local docs root: [cyan]{local_root}[/cyan]")
        else:
            console.print("Local docs root: not available in this installation")
            console.print("GitHub docs: [cyan]https://github.com/sunwbeck/homebase/tree/main/docs[/cyan]")
        return

    entry = get_doc(doc)
    if entry is None:
        raise typer.BadParameter(f"unknown doc: {doc}")

    console.print(f"[bold]{entry.title}[/bold]")
    local_root = docs_root()
    local_path = local_root / entry.filename if local_root is not None else None
    if local_path is not None:
        console.print(f"path: {local_path}")
    console.print(f"github: {entry.url}")
    console.print(f"summary: {entry.summary}")
    console.print("")
    if local_path is not None and local_path.exists():
        console.print(local_path.read_text(encoding="utf-8"))
    else:
        console.print("Local doc copy is not available in this installation.")


@app.command("docs", hidden=True)
def docs_alias_command(doc: str | None = typer.Argument(None, help="Optional docs key such as current-state.")) -> None:
    """Hidden alias for `homebase doc`."""
    docs_command(doc)


@connect_app.command("identity", hidden=True)
def client_identity_command() -> None:
    """Print the local homebase client discovery payload as JSON."""
    _require_role("managed")
    console.print(json.dumps(discovery_payload(), indent=2, sort_keys=True))


@connect_app.command("code")
def client_code_command(
    refresh: bool = typer.Option(False, "--refresh", help="Generate a new 8-digit pairing code before printing it."),
) -> None:
    """Print the current 8-digit pairing code for this managed node."""
    _require_role("managed")
    state = refresh_pair_code() if refresh else load_client_state()
    console.print(_format_pair_code(state.pair_code))


@connect_app.command("profile", hidden=True)
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


def _run_controller_service_forever() -> None:
    """Run the controller background service placeholder loop."""
    while True:
        time.sleep(3600)


@service_app.command("start")
def service_start_command(
    host: str = typer.Option("0.0.0.0", "--host", help="Listen address for the managed connect endpoint."),
    port: int = typer.Option(DEFAULT_CLIENT_PORT, "--port", help="Listen port for the managed connect endpoint."),
    foreground: bool = typer.Option(False, "--foreground", hidden=True),
) -> None:
    """Start the local background service for this node.

    Managed nodes expose the connect endpoint here.
    Controller nodes run the long-lived controller daemon here.
    """
    current_role = _current_runtime_role() or "managed"
    if foreground:
        if current_role == "managed":
            console.print(f"serving homebase client on {host}:{port}")
        else:
            console.print("running homebase controller service")
        try:
            if current_role == "managed":
                serve_client(host=host, port=port)
            else:
                _run_controller_service_forever()
        except OSError as exc:
            console.print(f"[red]service start failed:[/red] {exc}")
            raise typer.Exit(code=1)
        return
    running = connect_server_running()
    if running is not None:
        console.print(f"[yellow]homebase connect server already running[/yellow] on {running.host}:{running.port} (pid {running.pid})")
        return
    CONNECT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_handle = CONNECT_LOG_PATH.open("a", encoding="utf-8")
    env = os.environ.copy()
    command = [
        sys.executable,
        "-m",
        "homebase_cli.cli",
        "service",
        "start",
        "--host",
        host,
        "--port",
        str(port),
        "--foreground",
    ]
    process = subprocess.Popen(
        command,
        stdout=log_handle,
        stderr=log_handle,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
    )
    log_handle.close()
    save_connect_runtime(
        ConnectRuntime(
            pid=process.pid,
            host=host,
            port=port,
            started_at=datetime.now(UTC).isoformat(),
            log_path=str(CONNECT_LOG_PATH),
        )
    )
    time.sleep(0.2)
    if process.poll() is not None:
        clear_connect_runtime()
        console.print(f"[red]service start failed[/red]")
        console.print(f"log: {CONNECT_LOG_PATH}")
        raise typer.Exit(code=1)
    if current_role == "managed":
        console.print(f"serving homebase client on {host}:{port}")
    else:
        console.print("running homebase controller service")
    console.print(f"background pid: {process.pid}")
    console.print(f"log: {CONNECT_LOG_PATH}")


@service_app.command("status")
def service_status_command() -> None:
    """Show the local background service status on this node."""
    current_role = _current_runtime_role()
    table = Table(show_header=True, header_style="bold")
    table.add_column("Mode")
    table.add_column("State")
    table.add_column("Endpoint")
    table.add_column("PID")
    table.add_column("Log")
    if current_role == "managed":
        runtime = connect_server_running()
        if runtime is None:
            table.add_row("managed connect", "stopped", "", "", str(CONNECT_LOG_PATH))
            console.print(table)
            return
        table.add_row("managed connect", "running", f"{runtime.host}:{runtime.port}", str(runtime.pid), runtime.log_path)
        console.print(table)
        console.print(f"started at: {runtime.started_at}")
        return
    runtime = connect_server_running()
    if runtime is None:
        table.add_row("controller daemon", "stopped", "", "", str(CONNECT_LOG_PATH))
        console.print(table)
        return
    table.add_row("controller daemon", "running", "", str(runtime.pid), runtime.log_path)
    console.print(table)
    console.print(f"started at: {runtime.started_at}")


@service_app.command("stop")
def service_stop_command() -> None:
    """Stop the local background service on this node."""
    runtime = stop_connect_server()
    if runtime is None:
        console.print("service: stopped")
        return
    console.print(f"stopped service (pid {runtime.pid})")


def _run_init(role: str | None = None, name: str | None = None) -> None:
    """Initialize this installation with a local role and node name."""
    if role is None:
        console.print("[bold]Initial setup[/bold]")
        console.print("homebase needs two local values before normal use:")
        console.print("1. runtime role: controller or managed")
        console.print("2. node name: the name shown in status, inventory, and pairing output")
    selected = role.strip().lower() if role is not None else _choose_runtime_role()
    selected_name = (name.strip() if name is not None else "") or typer.prompt(
        "Local node name",
        default=_current_node_name() or socket.gethostname(),
    )
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


@app.command("init")
def init_command(
    role: str | None = typer.Option(None, "--role", help="Optional node type to set directly: controller or managed."),
    name: str | None = typer.Option(None, "--name", help="Optional local node name to register directly."),
) -> None:
    """Initialize this installation by setting the local runtime role and node name."""
    _run_init(role=role, name=name)


@role_app.command("show")
def role_show_command(target: str | None = typer.Argument(None, help="Optional node name.")) -> None:
    """Show the local runtime role, or the role of one registered node."""
    if target is None:
        _print_local_role()
        return
    _require_role("controller")
    node = find_node(target)
    if node is None:
        raise typer.BadParameter(f"unknown node: {target}")
    console.print(f"node: {node.name}")
    console.print(f"role: {node.runtime_role}")
    console.print(f"kind: {node.kind}")


@role_app.command("list")
def role_list_command() -> None:
    """List all registered nodes with their runtime roles."""
    _require_role("controller")
    nodes = _inventory_nodes()
    if not nodes:
        console.print("registered nodes: none")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("Node")
    table.add_column("Role")
    table.add_column("Kind")
    table.add_column("Address")
    table.add_column("Groups")
    for node in nodes:
        table.add_row(
            _node_label(node.name),
            node.runtime_role,
            node.kind,
            node.address or "",
            ", ".join(node.role_groups) if node.role_groups else "",
        )
    console.print(table)


@role_app.command("edit")
def role_edit_command(
    target_or_role: str = typer.Argument(..., help="Local role value, or a node name when editing a registered node."),
    runtime_role: str | None = typer.Argument(None, help="New role for the selected registered node."),
) -> None:
    """Edit the local runtime role, or the role of one registered node."""
    if runtime_role is None:
        _set_local_role(target_or_role)
        return
    _require_role("controller")
    try:
        updated = set_node_runtime_role(target_or_role, runtime_role)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"[green]Set node role:[/green] {updated.name} -> {updated.runtime_role}")


@node_app.command("edit")
def node_edit_command(
    target: str = typer.Argument(..., help="Current node name."),
    field: str = typer.Argument(..., help="Field to edit: name or role."),
    value: str = typer.Argument(..., help="New value."),
) -> None:
    """Edit one registered node."""
    normalized = field.strip().lower()
    if normalized == "name":
        _require_role("controller")
        node = find_node(target)
        if node is None:
            raise typer.BadParameter(f"unknown node: {target}")
        try:
            renamed = rename_node(node.name, value)
            if _current_node_name() == node.name:
                set_node_name(renamed.name)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        console.print(f"[green]Renamed node:[/green] {node.name} -> {renamed.name}")
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
def group_list_command() -> None:
    """List all defined groups for comparison."""
    _require_role("controller")
    groups = load_role_groups()
    if not groups:
        console.print("defined groups: none")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("Group")
    table.add_column("Parents")
    table.add_column("Children")
    table.add_column("Nodes")
    table.add_column("Description")
    for group in groups:
        table.add_row(
            group.name,
            ", ".join(_group_parents(group.name)),
            str(len(group.members)),
            str(len(_assigned_group_nodes(group.name))),
            group.description,
        )
    console.print(table)


@group_app.command("show")
def group_show_command(group: str = typer.Argument(..., help="Group name.")) -> None:
    """Show one group in detail."""
    _require_role("controller")
    _show_group_details(group)


@group_app.command("add")
def group_add_command(group: str = typer.Argument(..., help="New group name.")) -> None:
    """Create one new group."""
    _require_role("controller")
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
    _require_role("controller")
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
    _require_role("controller")
    try:
        remove_role_group(group)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"[green]Removed group:[/green] {group.strip().lower()}")


@link_app.command("list")
def link_list_command() -> None:
    """List all parent-child links between groups."""
    _require_role("controller")
    groups = load_role_groups()
    table = Table(show_header=True, header_style="bold")
    table.add_column("Parent")
    table.add_column("Child")
    table.add_column("Child Nodes")
    table.add_column("Child Groups")
    rows = []
    for item in groups:
        for member in item.members:
            child = _find_group(member)
            rows.append((item.name, member, str(len(_assigned_group_nodes(member))), str(len(child.members) if child is not None else 0)))
    if not rows:
        console.print("group links: none")
        return
    for parent, child, node_count, child_groups in rows:
        table.add_row(parent, child, node_count, child_groups)
    console.print(table)


@link_app.command("show")
def link_show_command(parent: str = typer.Argument(..., help="Parent group name.")) -> None:
    """Show all direct child links for one parent group."""
    _require_role("controller")
    group = _find_group(parent)
    if group is None:
        raise typer.BadParameter(f"unknown group: {parent}")
    if not group.members:
        console.print("group links: none")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("Parent")
    table.add_column("Child")
    table.add_column("Child Nodes")
    table.add_column("Child Groups")
    for member in group.members:
        child = _find_group(member)
        table.add_row(parent, member, str(len(_assigned_group_nodes(member))), str(len(child.members) if child is not None else 0))
    console.print(table)


@link_app.command("add")
def link_add_command(
    parent: str = typer.Argument(..., help="Parent group."),
    child: str = typer.Argument(..., help="Child group."),
) -> None:
    """Link one child group beneath one parent group."""
    _require_role("controller")
    try:
        link_role_group(parent, child)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"[green]Linked group:[/green] {child.strip()} -> {parent.strip()}")


@link_app.command("remove")
def link_remove_command(
    parent: str = typer.Argument(..., help="Parent group."),
    child: str = typer.Argument(..., help="Child group."),
) -> None:
    """Remove one parent-child link between groups."""
    _require_role("controller")
    try:
        unlink_role_group(parent, child)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"[green]Unlinked group:[/green] {child.strip()} from {parent.strip()}")


@node_app.command("assign")
def node_assign_command(
    node: str = typer.Argument(..., help="Node name."),
    group: str = typer.Argument(..., help="Group name."),
) -> None:
    """Assign one node to one group."""
    _require_role("controller")
    try:
        assign_node_role_group(node, group)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"[green]Assigned node:[/green] {node} -> {group}")


@node_app.command("unassign")
def node_unassign_command(
    node: str = typer.Argument(..., help="Node name."),
    group: str = typer.Argument(..., help="Group name."),
) -> None:
    """Remove one node from one group."""
    _require_role("controller")
    try:
        unassign_node_role_group(node, group)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"[green]Removed node assignment:[/green] {node} from {group}")


@inventory_app.command("show")
def inventory_show_command() -> None:
    """Render and print the ansible inventory YAML."""
    _require_role("controller")
    target = write_ansible_inventory()
    console.print(f"[green]Inventory YAML:[/green] {target}")
    console.print(target.read_text(encoding="utf-8"))


@inventory_app.command("edit")
def inventory_edit_command() -> None:
    """Render the ansible inventory YAML and open it in `$EDITOR`."""
    _require_role("controller")
    target = open_ansible_inventory()
    console.print(f"[green]Opened ansible inventory:[/green] {target}")


@state_app.command("show")
def state_show_command(resource: str = typer.Argument(..., help="Resource path such as host.app.")) -> None:
    """Show saved operator state values for one registered node."""
    _require_role("controller")
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
    """Set one saved operator state value on one registered node."""
    _require_role("controller")
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
    """Remove one saved operator state value from one registered node."""
    _require_role("controller")
    try:
        unset_node_state(resource, key)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"[green]Removed state:[/green] {resource} {key}")


@package_app.command("status")
def package_status_command(
    resource: str | None = typer.Argument(None, help="Optional resource path."),
) -> None:
    """Show the installed homebase revision on this node or on one managed node."""
    if resource is not None:
        _require_role("controller")
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
    """Install one GitHub ref, or choose one interactively."""
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
        _require_role("controller")
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
    """Update to the latest available GitHub target."""
    try:
        latest = latest_github_version(repo_url, include_prerelease=include_prerelease)
    except RuntimeError as exc:
        console.print(f"[red]Latest-version lookup failed.[/red] {exc}")
        raise typer.Exit(code=1)
    console.print(f"[bold]Selected latest target:[/bold] {latest.version}")
    console.print(f"summary: {latest.summary}")
    if resource is not None:
        _require_role("controller")
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


def _build_node_app() -> typer.Typer:
    runtime_node_app = typer.Typer(invoke_without_command=True, help=node_app.info.help)
    runtime_node_app.callback()(node_callback)
    runtime_node_app.command("list")(node_list_command)
    runtime_node_app.command("show")(node_show_command)
    runtime_node_app.command("edit")(node_edit_command)
    runtime_node_app.command("assign")(node_assign_command)
    runtime_node_app.command("unassign")(node_unassign_command)
    return runtime_node_app


def _build_connect_app() -> typer.Typer:
    runtime_connect_app = typer.Typer(invoke_without_command=True, help=connect_app.info.help)
    runtime_connect_app.callback()(connect_callback)
    current_role = _current_runtime_role()
    if current_role != "managed":
        runtime_connect_app.command("scan")(node_scan_command)
        runtime_connect_app.command("add")(node_add_command)
    if current_role != "controller":
        runtime_connect_app.command("code")(client_code_command)
    runtime_connect_app.command("status")(connect_status_command)
    runtime_connect_app.command("profile", hidden=True)(client_profile_command)
    runtime_connect_app.command("identity", hidden=True)(client_identity_command)
    return runtime_connect_app


def _build_group_app() -> typer.Typer:
    runtime_group_app = typer.Typer(invoke_without_command=True, help=group_app.info.help)
    runtime_group_app.callback()(group_callback)
    runtime_group_app.command("list")(group_list_command)
    runtime_group_app.command("show")(group_show_command)
    runtime_group_app.command("add")(group_add_command)
    runtime_group_app.command("edit")(group_edit_command)
    runtime_group_app.command("remove")(group_remove_command)
    return runtime_group_app


def _build_link_app() -> typer.Typer:
    runtime_link_app = typer.Typer(invoke_without_command=True, help=link_app.info.help)
    runtime_link_app.callback()(link_callback)
    runtime_link_app.command("list")(link_list_command)
    runtime_link_app.command("show")(link_show_command)
    runtime_link_app.command("add")(link_add_command)
    runtime_link_app.command("remove")(link_remove_command)
    return runtime_link_app


def _build_role_app() -> typer.Typer:
    runtime_role_app = typer.Typer(invoke_without_command=True, help=role_app.info.help)
    runtime_role_app.callback()(role_callback)
    runtime_role_app.command("list")(role_list_command)
    runtime_role_app.command("show")(role_show_command)
    runtime_role_app.command("edit")(role_edit_command)
    return runtime_role_app


def _build_package_app() -> typer.Typer:
    runtime_package_app = typer.Typer(
        invoke_without_command=True,
        help=package_app.info.help,
    )
    runtime_package_app.callback()(package_callback)
    runtime_package_app.command("status")(package_status_command)
    runtime_package_app.command("versions")(package_versions_command)
    runtime_package_app.command("version", hidden=True)(package_version_command)
    runtime_package_app.command("update")(package_update_command)
    runtime_package_app.command("install")(package_install_command)
    return runtime_package_app


def _build_dev_app() -> typer.Typer:
    runtime_dev_app = typer.Typer(help=dev_app.info.help)
    runtime_dev_app.command("self-test")(dev_self_test_command)
    runtime_dev_app.command("docs")(docs_command)
    runtime_dev_app.add_typer(ansible_app, name="ansible")
    return runtime_dev_app


def _build_root_app() -> typer.Typer:
    runtime_app = typer.Typer(no_args_is_help=True, help=app.info.help)
    runtime_app.command("init")(init_command)
    runtime_app.command("status")(status_command)
    runtime_app.command("doc")(docs_command)
    runtime_app.command("docs", hidden=True)(docs_alias_command)
    runtime_app.add_typer(_build_role_app(), name="role")
    runtime_app.add_typer(_build_node_app(), name="node")
    runtime_app.add_typer(_build_group_app(), name="group")
    runtime_app.add_typer(_build_link_app(), name="link")
    runtime_app.add_typer(inventory_app, name="inventory")
    runtime_app.add_typer(_build_connect_app(), name="connect")
    runtime_app.add_typer(service_app, name="service")
    runtime_app.add_typer(state_app, name="state")
    runtime_app.add_typer(_build_package_app(), name="package")
    runtime_app.add_typer(_build_dev_app(), name="dev")
    return runtime_app


app = _build_root_app()


def main() -> None:
    """Run the CLI app."""
    if len(sys.argv) == 1 and _needs_initialization():
        console.print("[yellow]homebase is not initialized yet. Starting init...[/yellow]")
        _run_init()
        return
    app()


if __name__ == "__main__":
    main()
