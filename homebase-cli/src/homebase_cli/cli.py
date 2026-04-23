"""Typer entry point for the homebase CLI."""

from __future__ import annotations

import json
from pathlib import Path
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
from homebase_cli.inventory import ansible_ping, write_ansible_inventory
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
from homebase_cli.registry import add_node, child_nodes, load_nodes
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
from homebase_cli.settings import add_role, list_roles, load_settings, remove_role, set_role


app = typer.Typer(no_args_is_help=True, help="Read and inspect the NAS-backed homebase docs.")
node_app = typer.Typer(help="Register and inspect real nodes stored in the local registry.")
ansible_app = typer.Typer(help="Generate inventory and run small ansible actions against registered nodes.")
client_app = typer.Typer(help="Run the homebase client identity endpoint on one node.")
package_app = typer.Typer(
    invoke_without_command=True,
    help="Inspect GitHub versions and install or update homebase from GitHub refs.",
)
dev_app = typer.Typer(help="Run local development validation commands.")
app.add_typer(node_app, name="node")
app.add_typer(ansible_app, name="ansible")
app.add_typer(client_app, name="client")
app.add_typer(package_app, name="package")
app.add_typer(dev_app, name="dev")
console = Console()
DEFAULT_KIND_CHOICES = ("control", "workstation", "host", "vm", "node")


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


def _choose_or_add_role() -> str:
    configured_roles = list(list_roles())
    selected = _pick_from_list("Node role", [*configured_roles, "Add new role"])
    if selected == "Add new role":
        new_role = typer.prompt("New role").strip().lower()
        if not new_role:
            raise typer.BadParameter("new role cannot be empty")
        add_role(new_role)
        return new_role
    return selected


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


@node_app.command("scan")
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


@node_app.command("add")
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


@node_app.command("status")
def node_status_command() -> None:
    """Show the registered node hierarchy."""
    _require_role("control")
    rows = []
    for node in all_resources():
        rows.append((f'{"  " * node.depth}{node.name}', node.kind))
    if not rows:
        console.print("[yellow]No nodes registered yet.[/yellow]")
        return
    print_node_tree(rows)


@app.command("status")
def status_command(
    resource: str | None = typer.Argument(None, help="Optional resource path such as host.app."),
) -> None:
    """Show currently registered nodes."""
    _require_role("control")
    if resource is None:
        rows = [(node.name, node.kind, node.address or "") for node in load_nodes()]
        if not rows:
            console.print("[yellow]No nodes registered yet.[/yellow]")
            return
        print_resource_table(rows)
        return
    info_command(resource)


@app.command("ls")
def ls_command(resource: str | None = typer.Argument(None, help="Optional resource path such as host.")) -> None:
    """List top-level or child resources."""
    _require_role("control")
    try:
        resources = child_resources(resource)
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc

    rows = [(item.name, item.kind, item.address or "") for item in resources]
    if not rows:
        console.print(f"[yellow]No children under {resource}.[/yellow]")
        return
    print_resource_table(rows)


@app.command("info")
def info_command(resource: str = typer.Argument(..., help="Canonical resource path.")) -> None:
    """Show high-level information for a resource."""
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
    """Render the current node registry as an ansible inventory."""
    _require_role("control")
    if output is None:
        output = Path("inventory.ini")
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
    _require_role("client")
    console.print(json.dumps(discovery_payload(), indent=2, sort_keys=True))


@client_app.command("code")
def client_code_command(
    refresh: bool = typer.Option(False, "--refresh", help="Generate a new 8-digit pairing code before printing it."),
) -> None:
    """Print the current local pairing code."""
    _require_role("client")
    state = refresh_pair_code() if refresh else load_client_state()
    console.print(state.pair_code)


@client_app.command("profile")
def client_profile_command() -> None:
    """Print the local paired profile only for local inspection."""
    _require_role("client")
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
    _require_role("client")
    console.print(f"serving homebase client on {host}:{port}")
    serve_client(host=host, port=port)


@app.command("init")
def init_command(
    role: str | None = typer.Option(None, "--role", help="Optional role to set directly. If omitted, choose from configured roles or add a new one."),
) -> None:
    """Initialize the local node role for this homebase installation."""
    selected = role.strip().lower() if role is not None else _choose_or_add_role()
    if role is not None and selected not in list_roles():
        add_role(selected)
    updated = set_role(selected)
    console.print(f"[green]Set local role to {updated.role}[/green]")


@app.command("role")
def role_command(
    value: str | None = typer.Argument(None, help="Optional new role: control or client."),
) -> None:
    """Show or update the local node role."""
    if value is None:
        current = load_settings().role
        console.print(current or "unconfigured")
        return
    selected = value.strip().lower()
    if selected not in list_roles():
        add_role(selected)
    updated = set_role(selected)
    console.print(f"[green]Set local role to {updated.role}[/green]")


@app.command("roles")
def roles_command(
    add: str | None = typer.Option(None, "--add", help="Add one selectable role."),
    remove: str | None = typer.Option(None, "--remove", help="Remove one selectable role."),
) -> None:
    """List, add, or remove selectable local roles."""
    if add and remove:
        raise typer.BadParameter("use only one of --add or --remove at a time")
    if add is not None:
        updated = add_role(add)
        console.print(f"[green]Added role:[/green] {add.strip().lower()}")
        for item in updated.roles:
            console.print(item)
        return
    if remove is not None:
        updated = remove_role(remove)
        console.print(f"[green]Removed role:[/green] {remove.strip().lower()}")
        for item in updated.roles:
            console.print(item)
        return
    for item in list_roles():
        console.print(item)


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


def main() -> None:
    """Run the CLI app."""
    app()


if __name__ == "__main__":
    main()
