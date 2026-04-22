"""Typer entry point for the homebase CLI."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Callable, Sequence

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
    PackageOperationError,
    build_duplicate_wheel,
    build_wheel,
    built_wheels,
    delete_package,
    install_command,
    install_wheel,
    installable_packages,
    latest_recovery_wheel,
    publish_bootstrap,
    publish_root,
    project_version,
    record_package,
    resolve_dist_dir,
    resolve_wheel,
)
from homebase_cli.registry import add_node, child_nodes, load_nodes
from homebase_cli.resources import all_resources, child_resources, find_resource
from homebase_cli.scanner import (
    detect_scannable_networks,
    pair_with_client,
    save_discovered_nodes,
    scan_for_clients,
    DiscoveredNode,
    unregistered_discovered_nodes,
)
from homebase_cli.selftest import run_client_self_test
from homebase_cli.settings import add_role, list_roles, load_settings, remove_role, set_package_location, set_role


app = typer.Typer(no_args_is_help=True, help="Read and inspect the NAS-backed homebase docs.")
node_app = typer.Typer(help="Register and inspect real nodes stored in the local registry.")
ansible_app = typer.Typer(help="Generate inventory and run small ansible actions against registered nodes.")
client_app = typer.Typer(help="Run the homebase client identity endpoint on one node.")
package_app = typer.Typer(
    invoke_without_command=True,
    help=(
        "Manage homebase package location, build wheels, publish first-install artifacts, "
        "and install updates."
    )
)
package_location_app = typer.Typer(
    invoke_without_command=True,
    help="Show or update the stored package directory used by package commands.",
)
dev_app = typer.Typer(help="Run local development validation commands.")
app.add_typer(node_app, name="node")
app.add_typer(ansible_app, name="ansible")
app.add_typer(client_app, name="client")
app.add_typer(package_app, name="package")
package_app.add_typer(package_location_app, name="location")
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


@package_location_app.callback()
def package_location_callback(ctx: typer.Context) -> None:
    """Show standard help when package location is called without a subcommand."""
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit(code=0)


@package_app.callback()
def package_callback(ctx: typer.Context) -> None:
    """Show standard help when package is called without a subcommand."""
    if ctx.invoked_subcommand is not None:
        return
    console.print(ctx.get_help())
    raise typer.Exit(code=0)


def _prompt_package_location(current: str | None) -> str | None:
    default_value = current or ""
    prompt_text = "Package location"
    if current:
        prompt_text += " (blank to keep current)"
    response = typer.prompt(prompt_text, default=default_value, show_default=bool(current)).strip()
    if not response:
        return current
    return str(Path(response).expanduser().resolve())


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
    package_location: str | None = typer.Option(None, "--package-location", help="Optional shared package directory to store during initialization."),
) -> None:
    """Initialize the local node role for this homebase installation."""
    current = load_settings()
    selected = role.strip().lower() if role is not None else _choose_or_add_role()
    if role is not None and selected not in list_roles():
        add_role(selected)
    updated = set_role(selected)
    if package_location is not None:
        resolved_location = str(Path(package_location).expanduser().resolve())
        updated = set_package_location(resolved_location)
    elif role is None:
        chosen_location = _prompt_package_location(current.package_location)
        if chosen_location != current.package_location:
            updated = set_package_location(chosen_location)
    console.print(f"[green]Set local role to {updated.role}[/green]")
    if updated.package_location:
        console.print(f"[green]Package location:[/green] {updated.package_location}")


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


@package_location_app.command("show")
def package_location_show_command() -> None:
    """Show the stored package directory."""
    console.print(resolve_dist_dir())


@package_location_app.command("set")
def package_location_set_command(
    path: Path = typer.Argument(..., help="Directory to store wheel artifacts."),
) -> None:
    """Set the stored package directory."""
    updated = set_package_location(str(path.resolve()))
    console.print(f"[green]Package location set to[/green] {updated.package_location}")


@package_location_app.command("clear")
def package_location_clear_command() -> None:
    """Clear the stored package directory and fall back to the default local dist directory."""
    set_package_location(None)
    console.print(f"[green]Package location cleared[/green]")
    console.print(resolve_dist_dir())


def _build_package_wheel(
    dist_dir: Path | None = typer.Option(None, "--dist-dir", help="Explicit directory where built wheel files will be written."),
    message: str = typer.Option("", "--message", help="Short release note stored with the built wheel."),
) -> None:
    """Build a wheel for repeated install and upgrade testing on nodes."""
    resolved_dir = resolve_dist_dir(dist_dir.resolve() if dist_dir is not None else None)
    version = project_version()
    same_version_paths = [path for path in built_wheels(resolved_dir) if f"-{version}-" in path.name]
    build_mode = "normal"
    console.print(f"[bold]Building version:[/bold] {version}")
    if same_version_paths:
        console.print(f"[yellow]Existing wheel with same version:[/yellow] {same_version_paths[0]}")
        if _is_interactive():
            selected = _pick_from_list(
                "Same version wheel already exists",
                (
                    "Replace existing wheel (Recommended)",
                    "Create duplicate wheel with same version",
                    "Keep existing wheel and skip build",
                ),
            )
            if selected.startswith("Create duplicate"):
                build_mode = "duplicate"
            elif selected.startswith("Keep existing"):
                console.print(f"[yellow]Skipped build.[/yellow] Keeping {same_version_paths[0]}")
                return
            else:
                build_mode = "replace"
                for existing_path in same_version_paths:
                    existing_path.unlink(missing_ok=True)
        else:
            console.print(
                "[red]Build blocked.[/red] Same-version wheel already exists. "
                "Re-run interactively and choose replace/duplicate/keep, or bump the version first."
            )
            raise typer.Exit(code=1)
    else:
        console.print(f"[green]No existing wheel with version {version} in {resolved_dir}[/green]")

    stage_titles = {
        "prepare": f"1/5 Prepare output directory: {resolved_dir}",
        "clean": "2/5 Clean previous build workspace",
        "select": "3/5 Select Python build backend",
        "run": "4/5 Build wheel artifact",
        "record": "5/5 Record build metadata",
    }

    def run_stage(title: str, work: Callable[[Callable[[], None]], None] | None = None) -> None:
        with Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task_id = progress.add_task(title, total=100)
            if work is None:
                progress.update(task_id, completed=100)
                return
            tick_value = 0

            def stage_tick() -> None:
                nonlocal tick_value
                if tick_value < 92:
                    tick_value += 1
                progress.update(task_id, completed=tick_value)

            work(stage_tick)
            progress.update(task_id, completed=100)

    run_stage(stage_titles["prepare"])
    run_stage(stage_titles["clean"])
    run_stage(stage_titles["select"])

    wheel_path: Path | None = None

    def build_stage(on_tick: callable) -> None:
        nonlocal wheel_path
        try:
            if build_mode == "duplicate":
                wheel_path = build_duplicate_wheel(resolved_dir, on_tick=on_tick)
            else:
                wheel_path = build_wheel(resolved_dir, on_stage=None, on_tick=on_tick)
        except PackageOperationError as exc:
            console.print(f"[red]Package build failed.[/red] Log: {exc.log_path}")
            console.print(f"Check the log with: `less {exc.log_path}`")
            raise typer.Exit(code=1)

    run_stage(stage_titles["run"], build_stage)

    final_message = message
    if not final_message and _is_interactive():
        final_message = typer.prompt("Build message (optional)", default="", show_default=False).strip()

    def record_stage(_: callable) -> None:
        assert wheel_path is not None
        record_package(wheel_path, message=final_message)

    run_stage(stage_titles["record"], record_stage)
    assert wheel_path is not None
    console.print(f"[green]Built package:[/green] {wheel_path}")
    console.print(f"[green]Built version:[/green] {version}")


@package_app.command("build")
def package_build_command(
    dist_dir: Path | None = typer.Option(None, "--dist-dir", help="Explicit directory where built wheel files will be written."),
    message: str = typer.Option("", "--message", help="Short release note stored with the built wheel."),
) -> None:
    """Build a wheel package for install and recovery."""
    _build_package_wheel(dist_dir=dist_dir, message=message)


@package_app.command("wheel", hidden=True)
def package_wheel_command(
    dist_dir: Path | None = typer.Option(None, "--dist-dir", help="Explicit directory where built wheel files will be written."),
    message: str = typer.Option("", "--message", help="Short release note stored with the built wheel."),
) -> None:
    """Backward-compatible alias for `hb package build`."""
    _build_package_wheel(dist_dir=dist_dir, message=message)


@package_app.command("publish")
def package_publish_command(
    dist_dir: Path | None = typer.Option(None, "--dist-dir", help="Explicit package directory whose parent will receive published bootstrap artifacts."),
) -> None:
    """Publish bootstrap artifacts for first-install on fresh nodes."""
    with Progress(
        SpinnerColumn(),
        TextColumn("{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        resolved_dir = resolve_dist_dir(dist_dir.resolve() if dist_dir is not None else None)
        task = progress.add_task(f"1/3 Check package directory: {resolved_dir}", total=100)
        progress.update(task, completed=100)
        if not built_wheels(resolved_dir):
            task = progress.add_task("2/3 Build base package for publish", total=100)
            try:
                tick_value = 0

                def publish_tick() -> None:
                    nonlocal tick_value
                    if tick_value < 92:
                        tick_value += 1
                    progress.update(task, completed=tick_value)

                base_wheel = build_wheel(resolved_dir, on_tick=publish_tick)
                progress.update(task, completed=100)
                record_task = progress.add_task("3/3 Record published build metadata", total=100)
                record_package(base_wheel, message="publish bootstrap base package")
                progress.update(record_task, completed=100)
            except PackageOperationError as exc:
                progress.stop()
                console.print(f"[red]Package publish failed during build.[/red] Log: {exc.log_path}")
                console.print(f"Check the log with: `less {exc.log_path}`")
                raise typer.Exit(code=1)
        else:
            task = progress.add_task("2/3 Use existing built package", total=100)
            progress.update(task, completed=100)
        task = progress.add_task("3/3 Publish bootstrap artifacts", total=100)
        try:
            tick_value = 0

            def publish_artifact_tick() -> None:
                nonlocal tick_value
                if tick_value < 92:
                    tick_value += 1
                progress.update(task, completed=tick_value)

            script_path, bundle_path = publish_bootstrap(resolved_dir, on_tick=publish_artifact_tick)
        except Exception as exc:
            progress.stop()
            console.print(f"[red]Package publish failed.[/red] {exc}")
            raise typer.Exit(code=1)
        progress.update(task, completed=100)
    console.print(f"[green]Published bootstrap script:[/green] {script_path}")
    console.print(f"[green]Published source bundle:[/green] {bundle_path}")
    console.print(f"[green]Publish root:[/green] {publish_root(resolved_dir)}")
    console.print("[green]Fresh-node install command:[/green]")
    console.print(f"bash {script_path}")
    console.print("[green]Optional examples:[/green]")
    console.print(f"bash {script_path} --wheel-dir {resolved_dir}")
    console.print(f"bash {script_path} --package-location {resolved_dir}")
    console.print(f"bash {script_path} --role client")


@package_app.command("list")
def package_list_command(
    dist_dir: Path | None = typer.Option(None, "--dist-dir", help="Explicit directory to search when no wheel path is given."),
) -> None:
    """List installable wheel artifacts in the configured package directory."""
    resolved_dir = resolve_dist_dir(dist_dir.resolve() if dist_dir is not None else None)
    packages = installable_packages(resolved_dir)
    if not packages:
        console.print(f"[yellow]No wheels found in {resolved_dir}[/yellow]")
        return
    console.print(f"[bold]Package directory:[/bold] {resolved_dir}")
    table = Table(show_header=True, header_style="bold")
    table.add_column("#", justify="right")
    table.add_column("Version")
    table.add_column("Wheel File")
    table.add_column("Message")
    table.add_column("Created")
    for index, item in enumerate(packages, start=1):
        table.add_row(
            str(index),
            item.version,
            item.filename,
            item.message or "",
            item.created_at,
        )
    console.print(table)


def _choose_package_wheel(dist_dir: Path | None = None) -> Path:
    packages = installable_packages(dist_dir)
    if not packages:
        raise typer.BadParameter(f"no wheels found in {resolve_dist_dir(dist_dir)}")
    labels = [item.label for item in packages]
    selected = _pick_from_list("Installable wheels", labels)
    chosen = packages[labels.index(selected)]
    return resolve_wheel(dist_dir, chosen.filename)


def _choose_package_record(dist_dir: Path | None = None) -> PackageRecord:
    packages = installable_packages(dist_dir)
    if not packages:
        raise typer.BadParameter(f"no wheels found in {resolve_dist_dir(dist_dir)}")
    labels = [item.label for item in packages]
    selected = _pick_from_list("Installable wheels", labels)
    return packages[labels.index(selected)]


@package_app.command("install-command")
def package_install_command_command(
    wheel: str | None = typer.Option(None, "--wheel", help="Wheel filename in the package directory or an explicit wheel path."),
    dist_dir: Path | None = typer.Option(None, "--dist-dir", help="Explicit directory to search when no wheel path is given."),
    python_bin: str = typer.Option("python3", "--python", help="Python executable to use on the target node."),
) -> None:
    """Print the install command for one built wheel."""
    resolved_dir = resolve_dist_dir(dist_dir.resolve() if dist_dir is not None else None)
    try:
        resolved_wheel = resolve_wheel(resolved_dir, wheel)
    except ValueError as exc:
        console.print(f"[red]No installable package found.[/red] {exc}")
        console.print(f"Try `hb package build` or `hb package publish` first.")
        raise typer.Exit(code=1)
    console.print(install_command(resolved_wheel, python_bin=python_bin))
    if wheel is None and not built_wheels(resolved_dir):
        recovery = latest_recovery_wheel()
        if recovery is not None:
            console.print(f"[yellow]Using locally preserved recovery wheel:[/yellow] {recovery}")


@package_app.command("delete")
def package_delete_command(
    wheel: str | None = typer.Option(None, "--wheel", help="Wheel filename in the package directory or an explicit wheel path."),
    dist_dir: Path | None = typer.Option(None, "--dist-dir", help="Explicit directory to search when no wheel path is given."),
) -> None:
    """Delete one built wheel from the package directory and remove it from the package list."""
    resolved_dir = resolve_dist_dir(dist_dir.resolve() if dist_dir is not None else None)
    try:
        if wheel is not None:
            wheel_path = resolve_wheel(resolved_dir, wheel)
        else:
            chosen = _choose_package_record(resolved_dir)
            wheel_path = resolve_wheel(resolved_dir, chosen.filename)
    except ValueError as exc:
        console.print(f"[red]Package delete failed.[/red] {exc}")
        raise typer.Exit(code=1)

    delete_package(wheel_path, resolved_dir)
    console.print(f"[green]Deleted package:[/green] {wheel_path}")


@package_app.command("install")
def package_install_package_command(
    wheel: str | None = typer.Option(None, "--wheel", help="Wheel filename in the package directory or an explicit wheel path."),
    dist_dir: Path | None = typer.Option(None, "--dist-dir", help="Explicit directory to search when no wheel path is given."),
    python_bin: str | None = typer.Option(None, "--python", help="Explicit Python executable to install into. Defaults to the current Python environment."),
) -> None:
    """Install or upgrade homebase from one built wheel."""
    resolved_dir = resolve_dist_dir(dist_dir.resolve() if dist_dir is not None else None)
    try:
        resolved_wheel = resolve_wheel(resolved_dir, wheel) if wheel is not None else _choose_package_wheel(resolved_dir)
    except ValueError as exc:
        console.print(f"[red]Package install failed.[/red] {exc}")
        raise typer.Exit(code=1)

    with Progress(
        SpinnerColumn(),
        TextColumn("{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("1/2 Resolve package to install", total=100)
        progress.update(task, completed=100)
        task = progress.add_task(f"2/2 Install {resolved_wheel.name}", total=100)
        current_tick = 0

        def install_tick() -> None:
            nonlocal current_tick
            if current_tick < 92:
                current_tick += 1
            progress.update(task, completed=current_tick)

        result = install_wheel(resolved_wheel, python_bin=python_bin, on_tick=install_tick)
        if result.returncode != 0:
            progress.stop()
            console.print(f"[red]Package install failed.[/red] Log: {result.log_path}")
            console.print(f"Check the log with: `less {result.log_path}`")
            raise typer.Exit(code=1)
        progress.update(task, completed=100)
    console.print(f"[green]Installed wheel:[/green] {resolved_wheel}")
    if python_bin is not None:
        console.print(f"[green]Installed into Python:[/green] {python_bin}")


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
