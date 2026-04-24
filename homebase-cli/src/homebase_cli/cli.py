"""Typer entry point for the homebase CLI."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import secrets
import subprocess
import socket
import sys
from threading import Lock
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import click
import typer
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text
from rich.table import Table

from homebase_cli.client import (
    ConnectRuntime,
    CONNECT_LOG_PATH,
    DEFAULT_CLIENT_PORT,
    control_service,
    detect_exposed_endpoints,
    detect_service_records,
    describe_port,
    detect_primary_address,
    detect_running_services,
    connect_server_running,
    clear_connect_runtime,
    discovery_payload,
    local_profile,
    load_client_state,
    load_connect_runtime,
    normalize_pair_code,
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
    schedule_windows_self_update,
    should_defer_windows_self_update,
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
    remove_node,
    remove_role_group,
    set_role_group_description,
    set_node_runtime_role,
    unassign_node_role_group,
    unlink_role_group,
)
from homebase_cli.resources import all_resources, child_resources, find_resource
from homebase_cli.scanner import (
    detect_scannable_networks,
    fetch_package_progress,
    fetch_package_status,
    fetch_profile,
    load_discovered_nodes,
    PairingError,
    request_package_install,
    request_package_upgrade,
    request_service_action,
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
    set_node_description as set_local_node_description,
    set_node_name,
    set_role,
)


app = typer.Typer(
    no_args_is_help=True,
    help="Initialize this node, connect it to homebase, and inspect or manage the system.",
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
package_app = typer.Typer(
    invoke_without_command=True,
    help="Check the installed homebase revision and install or update from GitHub.",
)
dev_app = typer.Typer(help="Development, diagnostics, and internal helper commands.")
service_app = typer.Typer(
    invoke_without_command=True,
    help="Inspect exposed endpoints and running services.",
)
daemon_app = typer.Typer(
    invoke_without_command=True,
    help="Run the local background runtime on this node.",
)
console = Console()
DEFAULT_KIND_CHOICES = ("controller", "workstation", "host", "vm", "node")


def _show_group_help(ctx: typer.Context) -> None:
    """Show help instead of a missing-command error for command groups."""
    if ctx.invoked_subcommand is not None:
        return
    console.print(ctx.get_help())
    raise typer.Exit(code=0)


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


def _format_port_summary(ports: Sequence[int]) -> str:
    """Render ports as short 'port purpose' entries."""
    if not ports:
        return ""
    return ", ".join(f"{port} {describe_port(port)}" for port in ports)


def _format_exposure_summary(endpoints: Sequence[tuple[int, str, str | None]]) -> str:
    """Render exposed endpoints as concise purpose:port entries."""
    if not endpoints:
        return ""
    return ", ".join(f"{purpose}:{port}" for port, purpose, _ in endpoints)


def _format_endpoint_ports(endpoints: Sequence[tuple[int, str, str | None]]) -> str:
    """Render exposed endpoints as a concise port list."""
    if not endpoints:
        return ""
    return ", ".join(str(port) for port, _, _ in endpoints)


def _format_endpoint_details(endpoints: Sequence[tuple[int, str, str | None]]) -> str:
    """Render endpoint details for one node view."""
    if not endpoints:
        return ""
    return "\n".join(
        f"{port} -> {purpose}{f' ({owner})' if owner else ''}" for port, purpose, owner in endpoints
    )


def _normalize_service_key(value: str) -> str:
    """Return a comparable service key."""
    normalized = value.strip().lower()
    if normalized.endswith(".service"):
        normalized = normalized.removesuffix(".service")
    if "@" in normalized:
        normalized = normalized.split("@", 1)[0]
    return normalized


def _service_rows(snapshot: dict[str, object]) -> list[dict[str, object]]:
    """Return normalized service rows for one node snapshot."""
    endpoints = tuple(snapshot["endpoints"])
    endpoint_records = tuple(snapshot.get("endpoint_records") or ())
    endpoint_map: dict[str, list[tuple[int, str, str | None, int | None]]] = {}
    endpoint_pid_map: dict[int, list[tuple[int, str, str | None, int | None]]] = {}
    for endpoint in endpoint_records:
        port, purpose, owner, pid = endpoint
        for candidate in filter(None, {_normalize_service_key(purpose), _normalize_service_key(owner or "")}):
            endpoint_map.setdefault(candidate, []).append(endpoint)
        if pid is not None:
            endpoint_pid_map.setdefault(pid, []).append(endpoint)

    rows: list[dict[str, object]] = []
    matched_endpoints: set[tuple[int, str, str | None, int | None]] = set()
    for name, state, pid, kind, description in tuple(snapshot["service_records"]):
        key = _normalize_service_key(name)
        matched = tuple(endpoint_pid_map.get(pid, ())) if pid is not None else ()
        if not matched:
            matched = tuple(endpoint_map.get(key, ()))
        matched_endpoints.update(matched)
        rows.append(
            {
                "name": name,
                "state": state,
                "pid": pid,
                "kind": kind,
                "description": description,
                "endpoints": tuple((port, purpose, owner) for port, purpose, owner, _ in matched),
            }
        )

    for endpoint in endpoint_records:
        if endpoint in matched_endpoints:
            continue
        port, purpose, owner, pid = endpoint
        rows.append(
            {
                "name": purpose,
                "state": "listening",
                "pid": pid,
                "kind": "endpoint",
                "description": owner or "",
                "endpoints": ((port, purpose, owner),),
            }
        )
    return rows


def _service_row_matches_terms(
    node,
    snapshot: dict[str, object],
    row: dict[str, object],
    terms: Sequence[str],
) -> bool:
    """Return whether one service row matches all query terms."""
    ports = [str(port) for port, _, _ in tuple(row["endpoints"])]
    fields = [
        node.name,
        str(snapshot["address"] or ""),
        str(snapshot["hostname"] or ""),
        str(row["name"] or ""),
        str(row["state"] or ""),
        str(row["description"] or ""),
        *ports,
    ]
    lowered_fields = [field.lower() for field in fields if field]
    for term in terms:
        normalized = term.strip().lower()
        if not normalized:
            continue
        if not any(normalized in field for field in lowered_fields):
            return False
    return True


def _node_runtime_snapshot(node):
    """Return one normalized runtime snapshot for a node."""
    local_name = _current_node_name()
    is_local = bool(local_name and node.name == local_name)
    profile = None
    if is_local:
        try:
            profile = local_profile()
        except Exception:
            profile = None
    if not is_local and node.address and node.client_port:
        try:
            profile = fetch_profile(node.address, port=node.client_port)
        except Exception:
            profile = None
    endpoints = detect_exposed_endpoints() if profile is not None else (
        node.exposed_endpoints or tuple((port, describe_port(port), None) for port in node.open_ports)
    )
    endpoint_records = tuple(profile.endpoint_records) if profile is not None else (
        node.endpoint_records or tuple((port, purpose, owner, None) for port, purpose, owner in endpoints)
    )
    services = tuple(profile.services) if profile is not None else node.services
    all_services = tuple(detect_running_services()) if is_local else services
    service_records = tuple(profile.service_records) if profile is not None else (
        node.service_records or tuple((service, "running", None, "service", "") for service in services)
    )
    return {
        "address": node.address or (detect_primary_address() if is_local else None) or "",
        "hostname": node.runtime_hostname or (profile.hostname if profile is not None else "") or "",
        "platform": node.platform or (profile.platform if profile is not None else "") or "",
        "services": services,
        "all_services": all_services,
        "service_records": service_records,
        "endpoints": endpoints,
        "endpoint_records": endpoint_records,
        "is_local": is_local,
    }


def _inventory_nodes():
    """Return registered nodes, keeping the local node visible."""
    nodes = list(load_nodes())
    current_name = _current_node_name()
    current_role = _current_runtime_role()
    if current_name and current_role:
        try:
            profile = local_profile()
            local_node = ensure_local_node(
                current_name,
                current_role,
                runtime_hostname=profile.hostname,
                address=detect_primary_address() or None,
                platform=profile.platform,
                client_port=DEFAULT_CLIENT_PORT,
                open_ports=profile.open_ports,
                services=profile.services,
                exposed_endpoints=profile.exposed_endpoints,
                endpoint_records=profile.endpoint_records,
            )
        except Exception:
            local_node = ensure_local_node(current_name, current_role, runtime_hostname=socket.gethostname())
        nodes = [node for node in load_nodes() if node.name != local_node.name]
        nodes.append(local_node)
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
    details = [item.discovery.node_name, item.address]
    if item.discovery.hostname and item.discovery.hostname != item.discovery.node_name:
        details.append(item.discovery.hostname)
    if item.discovery.description:
        details.append(item.discovery.description)
    return " | ".join(details)


def _pick_from_list(label: str, options: Sequence[str]) -> str:
    if not options:
        raise typer.BadParameter(f"no options available for {label}")
    console.print(f"{label}:")
    for index, option in enumerate(options, start=1):
        console.print(f"{index}. {option}")
    response = typer.prompt("Select number or exact value", type=str).strip()
    if response.isdigit():
        choice = int(response)
        if choice < 1 or choice > len(options):
            raise typer.BadParameter(f"invalid selection: {choice}")
        return options[choice - 1]
    if response in options:
        return response
    raise typer.BadParameter(f"invalid selection: {response}")


def _format_pair_code(code: str) -> str:
    """Render one pairing code in easier-to-read blocks."""
    normalized = code.strip()
    if len(normalized) == 8 and normalized.isdigit():
        return f"{normalized[:4]} {normalized[4:]}"
    return normalized


def _format_pair_code_expiry(expires_at: str | None) -> str:
    """Render one pairing code expiry timestamp briefly."""
    if not expires_at:
        return ""
    try:
        value = datetime.fromisoformat(expires_at)
    except ValueError:
        return expires_at
    return value.astimezone().strftime("%Y-%m-%d %H:%M")


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


def _selected_package_nodes(
    resources: list[str] | None,
    groups: list[str] | None,
    all_nodes: bool,
) -> list:
    """Return selected package target nodes for controller batch actions."""
    _require_role("controller")
    resources = resources or []
    groups = groups or []
    inventory = list(_inventory_nodes())
    by_name = {node.name: node for node in inventory}
    selected: dict[str, object] = {}
    if all_nodes:
        selected.update(by_name)
    for group_name in groups:
        matched = [node for node in inventory if group_name in node.role_groups]
        if not matched:
            raise typer.BadParameter(f"unknown or empty group: {group_name}")
        for node in matched:
            selected[node.name] = node
    for resource in resources:
        node = by_name.get(resource)
        if node is None:
            raise typer.BadParameter(f"unknown node: {resource}")
        selected[node.name] = node
    return [selected[name] for name in sorted(selected)]


def _local_package_status_payload() -> dict[str, object]:
    """Return the current local package status as a payload-like mapping."""
    current = load_install_state()
    return {
        "installed_version": current.installed_version,
        "repo_url": current.repo_url or DEFAULT_REPO_URL,
        "requested_ref": current.requested_ref,
        "resolved_ref": current.resolved_ref,
        "summary": current.summary,
        "installed_at": current.installed_at,
    }


def _package_stage_text(
    *,
    description_prefix: str,
    node_name: str,
    step: int,
    total: int,
    label: str,
    status: str,
) -> str:
    """Render one package stage line for spinner-style progress output."""
    prefix = f"{description_prefix} {_node_label(node_name)}"
    if status == "done":
        return f"{prefix}: [{total}/{total}] done"
    if status == "failed":
        return f"{prefix}: [{step}/{total}] failed - {label}"
    if status == "waiting":
        return f"{prefix}: [{step}/{total}] queued"
    return f"{prefix}: [{step}/{total}] {label}"


def _package_active_text(*, description_prefix: str, node_name: str, label: str) -> str:
    """Render one active package spinner line."""
    return f"{description_prefix} {_node_label(node_name)}: {label}"


def _should_persist_package_stage(*, label: str, status: str) -> bool:
    """Return whether one stage should stay in the permanent package log."""
    if status in {"done", "failed"}:
        return True
    return label not in {"requesting remote update", "requesting remote install"}


def _render_package_panels(
    *,
    selected_nodes: list,
    node_logs: dict[str, list[str]],
    stage_state: dict[str, tuple[int, int, str, str]],
) -> Group:
    """Render grouped package progress panels, one per node."""
    panels = []
    for node in selected_nodes:
        logs = node_logs.get(node.name, [])
        step, total, label, status = stage_state.get(node.name, (1, 6, "waiting", "waiting"))
        body_items: list[object] = []
        if logs:
            body_items.append(Text("\n".join(logs)))
        if status in {"waiting", "running"}:
            body_items.append(
                Spinner(
                    "dots",
                    text=f"[{step}/{total}] {label}",
                )
            )
        elif not logs:
            body_items.append(Text("done" if status == "done" else status))
        panels.append(Panel(Group(*body_items), title=_node_label(node.name), expand=True))
    return Group(*panels)


def _print_package_stage(
    *,
    description_prefix: str,
    node_name: str,
    step: int,
    total: int,
    label: str,
    status: str,
    lock: Lock | None = None,
) -> None:
    """Print one persistent package stage line."""
    message = _package_stage_text(
        description_prefix=description_prefix,
        node_name=node_name,
        step=step,
        total=total,
        label=label,
        status=status,
    )
    if lock is None:
        console.print(message)
        return
    with lock:
        console.print(message)


def _log_package_stage(
    *,
    progress: Progress | None,
    description_prefix: str,
    node_name: str,
    step: int,
    total: int,
    label: str,
    status: str,
    lock: Lock | None = None,
) -> None:
    """Print one persistent package log line, optionally through a live progress renderer."""
    message = _package_stage_text(
        description_prefix=description_prefix,
        node_name=node_name,
        step=step,
        total=total,
        label=label,
        status=status,
    )
    if lock is None:
        if progress is None:
            console.print(message)
        else:
            progress.console.print(message)
        return
    with lock:
        if progress is None:
            console.print(message)
        else:
            progress.console.print(message)


def _consume_package_progress_events(
    *,
    progress_payload: dict[str, object] | None,
    seen_events: int,
    default_step: int,
    stage_callback,
) -> int:
    """Consume one package progress payload and replay any new stage events."""
    if progress_payload is None:
        return seen_events
    events = progress_payload.get("events")
    if isinstance(events, list):
        if seen_events < len(events):
            for event in events[seen_events:]:
                if not isinstance(event, dict):
                    continue
                step = int(event.get("step", default_step) or default_step)
                total = int(event.get("total", 6) or 6)
                label = str(event.get("label") or "running")
                status = str(event.get("status") or "running")
                stage_callback(step, total, label, status)
            return len(events)
        return seen_events
    step = int(progress_payload.get("step", default_step) or default_step)
    total = int(progress_payload.get("total", 6) or 6)
    label = str(progress_payload.get("label") or "running")
    status = str(progress_payload.get("status") or "running")
    stage_callback(step, total, label, status)
    return seen_events


def _run_package_batch(
    *,
    selected_nodes: list,
    description_prefix: str,
    worker,
    row_builder,
) -> list[tuple[str, ...]]:
    """Run one package batch worker across nodes in parallel and return table rows."""
    if not selected_nodes:
        return []
    rows: dict[str, tuple[str, ...]] = {}
    stage_state: dict[str, tuple[int, int, str, str]] = {}
    node_logs: dict[str, list[str]] = {node.name: [] for node in selected_nodes}
    stage_lock = Lock()
    max_workers = min(8, max(1, len(selected_nodes)))

    with Live(_render_package_panels(selected_nodes=selected_nodes, node_logs=node_logs, stage_state=stage_state), console=console, refresh_per_second=8) as live:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {}
            for node in selected_nodes:
                stage_state[node.name] = (1, 6, "queued", "waiting")
                stage_state[node.name] = (1, 6, "submitted", "running")
                live.update(_render_package_panels(selected_nodes=selected_nodes, node_logs=node_logs, stage_state=stage_state), refresh=True)

                def stage_callback(step: int, total: int, label: str, status: str = "running", *, _name=node.name) -> None:
                    current = stage_state.get(_name, (step, total, label, "running"))
                    previous = current[:3]
                    stage_state[_name] = (step, total, label, status)
                    if previous != (step, total, label) and _should_persist_package_stage(label=label, status=status):
                        node_logs.setdefault(_name, []).append(
                            _package_stage_text(
                                description_prefix=description_prefix,
                                node_name=_name,
                                step=step,
                                total=total,
                                label=label,
                                status=status,
                            )
                        )
                    with stage_lock:
                        live.update(
                            _render_package_panels(selected_nodes=selected_nodes, node_logs=node_logs, stage_state=stage_state),
                            refresh=True,
                        )

                future = executor.submit(worker, node, stage_callback)
                future_map[future] = node

            while future_map:
                finished = []
                for future, node in list(future_map.items()):
                    if future.done():
                        try:
                            payload = future.result()
                            rows[node.name] = row_builder(node, payload)
                            step, total, current_label, current_status = stage_state.get(node.name, (6, 6, "done", "running"))
                            stage_state[node.name] = (total, total, "done", "done")
                            done_line = _package_stage_text(
                                description_prefix=description_prefix,
                                node_name=node.name,
                                step=total,
                                total=total,
                                label="done",
                                status="done",
                            )
                            if current_status != "done" and (not node_logs.get(node.name) or node_logs[node.name][-1] != done_line):
                                node_logs.setdefault(node.name, []).append(done_line)
                            with stage_lock:
                                live.update(
                                    _render_package_panels(selected_nodes=selected_nodes, node_logs=node_logs, stage_state=stage_state),
                                    refresh=True,
                                )
                        except Exception as exc:
                            step, total, _, _ = stage_state.get(node.name, (1, 6, "failed", "running"))
                            stage_state[node.name] = (step, total, str(exc), "failed")
                            node_logs.setdefault(node.name, []).append(
                                _package_stage_text(
                                    description_prefix=description_prefix,
                                    node_name=node.name,
                                    step=step,
                                    total=total,
                                    label=str(exc),
                                    status="failed",
                                )
                            )
                            with stage_lock:
                                live.update(
                                    _render_package_panels(selected_nodes=selected_nodes, node_logs=node_logs, stage_state=stage_state),
                                    refresh=True,
                                )
                            raise
                        finished.append(future)
                for future in finished:
                    del future_map[future]
                if future_map:
                    time.sleep(0.1)
    return [rows[node.name] for node in selected_nodes if node.name in rows]


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
    snapshot = _node_runtime_snapshot(node)
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
    runtime.add_row("hostname", snapshot["hostname"])
    runtime.add_row("os", snapshot["platform"])
    runtime.add_row("service", _node_service_state(node.name))
    runtime.add_row("external services", ", ".join(snapshot["services"]))
    runtime.add_row("all services", ", ".join(snapshot["all_services"]))
    runtime.add_row("groups", ", ".join(node.role_groups) if node.role_groups else "")
    console.print("[bold]Runtime[/bold]")
    console.print(runtime)

    network = Table(show_header=False, box=None)
    network.add_column("Field", style="bold")
    network.add_column("Value")
    network.add_row("address", snapshot["address"])
    network.add_row("connect port", str(node.client_port) if node.client_port is not None else "")
    network.add_row("exposed endpoints", _format_endpoint_details(snapshot["endpoints"]))
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
    table.add_column("Exposure")
    table.add_column("Groups")
    if not nodes:
        console.print("registered nodes: none")
        return
    for node in nodes:
        snapshot = _node_runtime_snapshot(node)
        table.add_row(
            _node_label(node.name),
            node.runtime_role,
            snapshot["address"],
            snapshot["hostname"],
            snapshot["platform"],
            _node_service_state(node.name),
            _format_exposure_summary(snapshot["endpoints"]),
            ", ".join(node.role_groups) if node.role_groups else "",
        )
    console.print(table)


def _print_managed_overview() -> None:
    """Print one managed-node overview including this node and paired controllers."""
    local_name = _current_node_name() or socket.gethostname()
    runtime = connect_server_running()
    state = load_client_state()
    local_node = find_node(local_name) or ensure_local_node(local_name, _current_runtime_role() or "managed", runtime_hostname=socket.gethostname())
    snapshot = _node_runtime_snapshot(local_node)
    table = Table(show_header=True, header_style="bold")
    table.add_column("Node")
    table.add_column("Role")
    table.add_column("Address")
    table.add_column("Hostname")
    table.add_column("OS")
    table.add_column("Service")
    table.add_column("Exposure")
    table.add_row(
        f"{local_name} (local)",
        _current_runtime_role() or "managed",
        snapshot["address"],
        snapshot["hostname"],
        snapshot["platform"],
        "running" if runtime is not None else "stopped",
        _format_exposure_summary(snapshot["endpoints"]),
    )
    for controller in state.paired_controllers:
        table.add_row(
            controller.hostname or controller.controller_id,
            "controller",
            controller.address or "",
            controller.hostname or "",
            "",
            "",
            "",
        )
    console.print("[bold]Node status[/bold]")
    console.print(table)


def _print_local_role() -> None:
    settings = load_settings()
    console.print(f"role: {settings.role or 'not set'}")
    console.print(f"node: {settings.node_name or 'not set'}")
    console.print(f"description: {settings.node_description or ''}")


def _set_local_role(runtime_role: str) -> None:
    try:
        updated = set_role(runtime_role)
        local_name = _current_node_name()
        local_settings = load_settings()
        if local_name:
            ensure_local_node(
                local_name,
                updated.role or "managed",
                runtime_hostname=socket.gethostname(),
                description=local_settings.node_description or "",
            )
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


@service_app.callback()
def service_callback(ctx: typer.Context) -> None:
    """Show standard help when service is called without a subcommand."""
    if ctx.invoked_subcommand is not None:
        return
    console.print(ctx.get_help())
    raise typer.Exit(code=0)


@daemon_app.callback()
def daemon_callback(ctx: typer.Context) -> None:
    """Show standard help when daemon is called without a subcommand."""
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


def _choose_registered_node(label: str = "Registered nodes") -> str:
    nodes = _inventory_nodes()
    if not nodes:
        raise typer.BadParameter("no registered nodes available")
    labels = [_node_label(node.name) for node in nodes]
    selected = _pick_from_list(label, labels)
    return nodes[labels.index(selected)].name


def _choose_registered_group(label: str = "Groups") -> str:
    groups = list(load_role_groups())
    if not groups:
        raise typer.BadParameter("no groups available")
    selected = _pick_from_list(label, [group.name for group in groups])
    return selected


def _choose_discovered_node() -> DiscoveredNode:
    pending = list(unregistered_discovered_nodes())
    if not pending:
        raise typer.BadParameter("no unregistered discovered nodes found; run `homebase connect scan` first")
    labels = [_format_discovered_label(item) for item in pending]
    selected_label = _pick_from_list("Discovered nodes", labels)
    return pending[labels.index(selected_label)]


def _resolve_profile_for_node(selected: DiscoveredNode, client_port: int) -> object:
    pair_code = normalize_pair_code(typer.prompt("8-digit pairing code", type=str).strip())
    if len(pair_code) != 8 or not pair_code.isdigit():
        raise typer.BadParameter("pairing code must be exactly 8 digits")
    try:
        return pair_with_client(selected.address, pair_code, port=client_port)
    except PairingError as exc:
        raise typer.BadParameter(f"pairing failed: {exc}") from exc


@connect_app.command("scan")
def node_scan_command(
    cidr: str | None = typer.Argument(None, help="Optional CIDR to scan, such as 192.168.219.0/24."),
    port: int = typer.Option(DEFAULT_CLIENT_PORT, "--port", help="TCP port exposed by the homebase client."),
    timeout: float = typer.Option(0.35, "--timeout", help="Per-host timeout in seconds."),
) -> None:
    """Scan the local network for managed nodes that are running `homebase daemon start`.

    Run this on the controller before `homebase connect add`.
    """
    _require_role("controller")
    networks = (cidr,) if cidr is not None else detect_scannable_networks()
    if not networks:
        console.print("[yellow]No scannable IPv4 networks detected.[/yellow]")
        return

    discovered = []
    rows: list[tuple[str, str, str, str, str, str, str]] = []
    for network in networks:
        results = scan_for_clients(network, port=port, timeout=timeout)
        discovered.extend(results)
        for item in results:
            rows.append(
                (
                    item.address,
                    _match_registered_nodes(item.address, item.discovery.node_id),
                    item.discovery.node_name,
                    item.discovery.hostname,
                    item.discovery.description,
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
    parent: str | None = typer.Option(None, "--parent", help="Optional parent node such as host. Omit this during initial pairing unless you already know the hierarchy."),
    ssh_user: str | None = typer.Option(None, "--ssh-user", help="Optional SSH user to save for later remote commands. Omit this during initial pairing if unknown."),
    description: str = typer.Option("", "--description", help="Short human-readable description."),
    client_port: int = typer.Option(DEFAULT_CLIENT_PORT, "--client-port", help="TCP port exposed by the homebase client."),
) -> None:
    """Pair one discovered managed node and register it in the local inventory.

    Typical flow:
      1. On the managed node, run `homebase connect code` and `homebase daemon start`
      2. On the controller, run `homebase connect scan`
      3. Then run this command and enter the 8-digit pairing code
    """
    _require_role("controller")
    selected = _choose_discovered_node()
    profile = _resolve_profile_for_node(selected, client_port)
    resolved_name = name or typer.prompt(
        "Node name",
        default=(profile.node_name or selected.discovery.node_name or profile.hostname or selected.discovery.hostname),
    )
    resolved_parent = parent.strip() if parent is not None and parent.strip() else None
    resolved_ssh_user = ssh_user.strip() if ssh_user is not None and ssh_user.strip() else None
    resolved_description = description or typer.prompt(
        "Description",
        default=profile.description or "",
        show_default=bool(profile.description),
    ).strip()
    try:
        node = add_node(
            name=resolved_name,
            parent=resolved_parent,
            kind="node",
            address=selected.address,
            ssh_user=resolved_ssh_user,
            description=resolved_description,
            runtime_hostname=profile.hostname,
            node_id=profile.node_id,
            platform=profile.platform,
            client_port=client_port,
            open_ports=profile.open_ports,
            services=profile.services,
            exposed_endpoints=profile.exposed_endpoints,
            endpoint_records=profile.endpoint_records,
            service_records=profile.service_records,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"[green]Registered node {node.name}[/green]")


@connect_app.command("remove")
def connect_remove_command(
    node_name: str | None = typer.Argument(None, help="Registered node name to remove."),
) -> None:
    """Remove one registered node from the inventory.

    This is the same removal action exposed under `homebase node remove`.
    """
    _require_role("controller")
    selected_name = node_name or _choose_registered_node()
    if selected_name == (_current_node_name() or ""):
        raise typer.BadParameter("cannot remove the local controller node")
    try:
        removed = remove_node(selected_name)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"[green]Removed node:[/green] {removed.name}")


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
        table.add_column("Expires")
        table.add_column("Controller")
        table.add_column("Address")
        table.add_column("Hostname")
        table.add_column("Service")
        table.add_column("Endpoint")
        controllers = list(state.paired_controllers) or [None]
        for index, controller in enumerate(controllers):
            table.add_row(
                _format_pair_code(state.pair_code) if index == 0 else "",
                _format_pair_code_expiry(state.pair_code_expires_at) if index == 0 else "",
                controller.controller_id if controller is not None else "",
                controller.address if controller is not None and controller.address is not None else "",
                controller.hostname if controller is not None and controller.hostname is not None else "",
                "running" if runtime is not None and index == 0 else ("stopped" if index == 0 else ""),
                f"{runtime.host}:{runtime.port}" if runtime is not None and index == 0 else "",
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
    """Show the current node list for this installation.

    Controller nodes see all registered nodes.
    Managed nodes see this node and any paired controllers.
    """
    if _current_runtime_role() == "managed":
        _print_managed_overview()
        return
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
    table.add_column("Node", no_wrap=True)
    table.add_column("Role", no_wrap=True)
    table.add_column("Address", no_wrap=True)
    table.add_column("Hostname", no_wrap=True)
    table.add_column("Groups", overflow="fold")
    table.add_column("Description", overflow="ellipsis")
    current_name = _current_node_name()
    for item in resources:
        snapshot = _node_runtime_snapshot(item)
        address_value = str(snapshot["address"] or "")
        hostname_value = str(snapshot["hostname"] or "")
        if current_name and item.name == current_name:
            address_value = address_value or detect_primary_address() or ""
            hostname_value = hostname_value or socket.gethostname().strip() or ""
        table.add_row(
            _node_label(item.name),
            item.runtime_role,
            address_value or "",
            hostname_value,
            ", ".join(item.role_groups) if item.role_groups else "",
            item.description,
        )
    console.print(table)


@node_app.command("show")
def node_show_command(resource: str = typer.Argument(..., help="Canonical node name.")) -> None:
    """Show one node in detail.

    The detailed view includes identity, runtime, network, hierarchy, groups,
    and direct children.
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
def client_code_command() -> None:
    """Issue and print one fresh 8-digit pairing code for this managed node."""
    _require_role("managed")
    state = refresh_pair_code()
    console.print(_format_pair_code(state.pair_code))
    console.print("use this code in `homebase connect add`; spaces are optional when entering it")
    console.print(f"expires: {_format_pair_code_expiry(state.pair_code_expires_at)}")


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
            "exposed_endpoints": [
                {"port": port, "purpose": purpose, "owner": owner}
                for port, purpose, owner in profile.exposed_endpoints
            ],
        },
        indent=2,
        sort_keys=True,
    ))


def _run_controller_service_forever() -> None:
    """Run the controller background service placeholder loop."""
    while True:
        time.sleep(3600)


def _start_daemon_background(*, host: str, port: int, current_role: str) -> None:
    """Start the local daemon in the background and print the result."""
    CONNECT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_handle = CONNECT_LOG_PATH.open("a", encoding="utf-8")
    env = os.environ.copy()
    command = [
        sys.executable,
        "-m",
        "homebase_cli.cli",
        "daemon",
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


@daemon_app.command("start")
def daemon_start_command(
    host: str = typer.Option("0.0.0.0", "--host", help="Listen address for the managed connect endpoint."),
    port: int = typer.Option(DEFAULT_CLIENT_PORT, "--port", help="Listen port for the managed connect endpoint."),
    foreground: bool = typer.Option(False, "--foreground", hidden=True),
) -> None:
    """Start the local background runtime for this node.

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
    _start_daemon_background(host=host, port=port, current_role=current_role)


@daemon_app.command("status")
def daemon_status_command() -> None:
    """Show the local background runtime status on this node."""
    current_role = _current_runtime_role()
    local_name = _current_node_name() or socket.gethostname()
    address = detect_primary_address() or ""
    hostname = socket.gethostname().strip() or ""
    table = Table(show_header=True, header_style="bold")
    table.add_column("Node")
    table.add_column("Role")
    table.add_column("Address")
    table.add_column("Hostname")
    table.add_column("Mode")
    table.add_column("State")
    table.add_column("Endpoint")
    table.add_column("PID")
    table.add_column("Log")
    if current_role == "managed":
        runtime = connect_server_running()
        if runtime is None:
            table.add_row(local_name, "managed", address, hostname, "managed connect", "stopped", "", "", str(CONNECT_LOG_PATH))
            console.print(table)
            return
        table.add_row(
            local_name,
            "managed",
            address,
            hostname,
            "managed connect",
            "running",
            f"{runtime.host}:{runtime.port}",
            str(runtime.pid),
            runtime.log_path,
        )
        console.print(table)
        console.print(f"started at: {runtime.started_at}")
        return
    runtime = connect_server_running()
    if runtime is None:
        table.add_row(local_name, "controller", address, hostname, "controller daemon", "stopped", "", "", str(CONNECT_LOG_PATH))
        console.print(table)
        return
    table.add_row(local_name, "controller", address, hostname, "controller daemon", "running", "", str(runtime.pid), runtime.log_path)
    console.print(table)
    console.print(f"started at: {runtime.started_at}")


@daemon_app.command("stop")
def daemon_stop_command() -> None:
    """Stop the local background runtime on this node."""
    runtime = stop_connect_server()
    if runtime is None:
        console.print("daemon: stopped")
        return
    console.print(f"stopped daemon (pid {runtime.pid})")


@daemon_app.command("restart")
def daemon_restart_command(
    host: str | None = typer.Option(None, "--host", help="Listen address for the restarted daemon. Defaults to the current daemon host."),
    port: int | None = typer.Option(None, "--port", help="Listen port for the restarted daemon. Defaults to the current daemon port."),
) -> None:
    """Restart the local background runtime on this node."""
    current_role = _current_runtime_role() or "managed"
    runtime = connect_server_running()
    selected_host = host or (runtime.host if runtime is not None else "0.0.0.0")
    selected_port = port or (runtime.port if runtime is not None else DEFAULT_CLIENT_PORT)
    if runtime is not None:
        stop_connect_server()
        console.print(f"stopped daemon (pid {runtime.pid})")
    else:
        console.print("daemon was not running; starting a new instance")
    _start_daemon_background(host=selected_host, port=selected_port, current_role=current_role)


def _node_exposed_services(node) -> tuple[str, ...]:
    """Return exposed services for one node, using live local data when possible."""
    return tuple(_node_runtime_snapshot(node)["services"])


def _node_exposure_summary(node) -> str:
    """Return one short exposure summary for one node."""
    return _format_exposure_summary(_node_runtime_snapshot(node)["endpoints"])


@service_app.command("list")
def service_list_command(
    resource: str | None = typer.Argument(None, help="Optional node name."),
    group: str | None = typer.Option(None, "--group", help="Optional group name."),
) -> None:
    """List service records across nodes, including state, pid, and exposed ports."""
    current_role = _current_runtime_role()
    if current_role == "managed":
        if resource is not None or group is not None:
            raise typer.BadParameter("managed nodes can only list local services")
        local_name = _current_node_name() or socket.gethostname()
        local_node = find_node(local_name) or ensure_local_node(local_name, current_role or "managed", runtime_hostname=socket.gethostname())
        table = Table(show_header=True, header_style="bold")
        table.add_column("Node")
        table.add_column("Address")
        table.add_column("Service")
        table.add_column("State")
        table.add_column("PID")
        table.add_column("Ports")
        table.add_column("Description", overflow="fold")
        snapshot = _node_runtime_snapshot(local_node)
        rows = _service_rows(snapshot)
        if not rows:
            table.add_row(_node_label(local_node.name), snapshot["address"], "none", "", "", "", "")
        else:
            for row in rows:
                table.add_row(
                    _node_label(local_node.name),
                    snapshot["address"],
                    str(row["name"]),
                    str(row["state"]),
                    str(row["pid"] or ""),
                    _format_endpoint_ports(tuple(row["endpoints"])),
                    str(row["description"] or ""),
                )
        console.print(table)
        return

    _require_role("controller")
    nodes = list(_inventory_nodes())
    if group is not None:
        nodes = [node for node in nodes if group in node.role_groups]
    if resource is not None:
        nodes = [node for node in nodes if node.name == resource]
        if not nodes:
            raise typer.BadParameter(f"unknown node: {resource}")
    if not nodes:
        console.print("[yellow]No matching nodes.[/yellow]")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("Node")
    table.add_column("Address")
    table.add_column("Service")
    table.add_column("State")
    table.add_column("PID")
    table.add_column("Ports")
    table.add_column("Description", overflow="fold")
    table.add_column("Groups")
    for node in nodes:
        snapshot = _node_runtime_snapshot(node)
        rows = _service_rows(snapshot)
        groups_value = ", ".join(node.role_groups) if node.role_groups else ""
        if not rows:
            table.add_row(_node_label(node.name), snapshot["address"], "none", "", "", "", "", groups_value)
            continue
        for row in rows:
            table.add_row(
                _node_label(node.name),
                snapshot["address"],
                str(row["name"]),
                str(row["state"]),
                str(row["pid"] or ""),
                _format_endpoint_ports(tuple(row["endpoints"])),
                str(row["description"] or ""),
                groups_value,
            )
    console.print(table)


@service_app.command("show")
def service_show_command(
    resource: str | None = typer.Argument(None, help="Optional node name."),
) -> None:
    """Show service records and exposed endpoints for one node in detail."""
    current_role = _current_runtime_role()
    selected_name = resource
    if current_role == "managed":
        if resource is not None and resource != (_current_node_name() or ""):
            raise typer.BadParameter("managed nodes can only show local services")
        selected_name = _current_node_name() or socket.gethostname()
    else:
        _require_role("controller")
        selected_name = resource or _choose_registered_node()
    node = find_node(selected_name)
    if node is None:
        raise typer.BadParameter(f"unknown node: {selected_name}")
    local_node = _current_node_name() and node.name == _current_node_name()
    snapshot = _node_runtime_snapshot(node)
    console.print(f"[bold]Services: {_node_label(node.name)}[/bold]")
    table = Table(show_header=False, box=None)
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("hostname", snapshot["hostname"])
    table.add_row("address", snapshot["address"])
    table.add_row("groups", ", ".join(node.role_groups) if node.role_groups else "")
    table.add_row("exposure", _format_endpoint_details(snapshot["endpoints"]))
    console.print(table)
    records = _service_rows(snapshot)
    detail = Table(show_header=True, header_style="bold")
    detail.add_column("Service")
    detail.add_column("Kind")
    detail.add_column("State")
    detail.add_column("PID")
    detail.add_column("Exposure")
    detail.add_column("Description")
    if not records:
        detail.add_row("none", "", "", "", "", "")
    else:
        for row in records:
            detail.add_row(
                str(row["name"]),
                str(row["kind"]),
                str(row["state"]),
                str(row["pid"] or ""),
                _format_exposure_summary(tuple(row["endpoints"])),
                str(row["description"] or ""),
            )
    console.print("")
    console.print("[bold]Service records[/bold]")
    console.print(detail)


@service_app.command("search")
def service_search_command(
    terms: list[str] = typer.Argument(..., help="Search terms such as node, service, state, address, or port."),
) -> None:
    """Search services across nodes with one or more filter terms.

    Terms are combined with AND matching, in any order.
    """
    current_role = _current_runtime_role()
    table = Table(show_header=True, header_style="bold")
    table.add_column("Node")
    table.add_column("Address")
    table.add_column("Service")
    table.add_column("State")
    table.add_column("PID")
    table.add_column("Ports")
    table.add_column("Description", overflow="fold")
    candidates = []
    if current_role == "managed":
        local_name = _current_node_name() or socket.gethostname()
        local_node = find_node(local_name) or ensure_local_node(local_name, current_role or "managed", runtime_hostname=socket.gethostname())
        candidates = [local_node]
    else:
        _require_role("controller")
        candidates = list(_inventory_nodes())
    rows = 0
    for node in candidates:
        snapshot = _node_runtime_snapshot(node)
        for row in _service_rows(snapshot):
            if not _service_row_matches_terms(node, snapshot, row, terms):
                continue
            table.add_row(
                _node_label(node.name),
                snapshot["address"],
                str(row["name"]),
                str(row["state"]),
                str(row["pid"] or ""),
                _format_endpoint_ports(tuple(row["endpoints"])),
                str(row["description"] or ""),
            )
            rows += 1
    if rows == 0:
        console.print("[yellow]No matching services.[/yellow]")
        return
    console.print(table)


def _apply_service_action(node, service: str, action: str) -> None:
    """Apply one service start/stop action locally or remotely."""
    local_name = _current_node_name()
    if local_name and node.name == local_name:
        try:
            control_service(service, action)
        except Exception as exc:
            raise typer.BadParameter(str(exc)) from exc
        return
    if not node.address:
        raise typer.BadParameter(f"node has no address: {node.name}")
    port = node.client_port or DEFAULT_CLIENT_PORT
    payload = request_service_action(node.address, service=service, action=action, port=port)
    if payload is None:
        raise typer.BadParameter(f"service {action} failed on {node.name}")


@service_app.command("start")
def service_start_command(
    resource: str | None = typer.Argument(None, help="Optional node name."),
    service: str | None = typer.Argument(None, help="Service or container name."),
) -> None:
    """Start one service on one node."""
    current_role = _current_runtime_role()
    if current_role == "managed":
        selected_name = _current_node_name() or socket.gethostname()
        selected_service = service or resource or typer.prompt("Service name").strip()
    else:
        _require_role("controller")
        selected_name = resource or _choose_registered_node()
        selected_service = service or typer.prompt("Service name").strip()
    node = find_node(selected_name)
    if node is None:
        raise typer.BadParameter(f"unknown node: {selected_name}")
    _apply_service_action(node, selected_service, "start")
    console.print(f"[green]Started service:[/green] {selected_service} on {node.name}")


@service_app.command("stop")
def service_stop_command(
    resource: str | None = typer.Argument(None, help="Optional node name."),
    service: str | None = typer.Argument(None, help="Service or container name."),
) -> None:
    """Stop one service on one node."""
    current_role = _current_runtime_role()
    if current_role == "managed":
        selected_name = _current_node_name() or socket.gethostname()
        selected_service = service or resource or typer.prompt("Service name").strip()
    else:
        _require_role("controller")
        selected_name = resource or _choose_registered_node()
        selected_service = service or typer.prompt("Service name").strip()
    node = find_node(selected_name)
    if node is None:
        raise typer.BadParameter(f"unknown node: {selected_name}")
    _apply_service_action(node, selected_service, "stop")
    console.print(f"[green]Stopped service:[/green] {selected_service} on {node.name}")


def _run_init(role: str | None = None, name: str | None = None, description: str | None = None) -> None:
    """Initialize this installation with a local role and node name."""
    current_settings = load_settings()
    if role is None:
        console.print("[bold]Initial setup[/bold]")
        console.print("Choose how this node will participate in homebase.")
        console.print("1. local runtime role: controller or managed")
        console.print("2. local node name: used in status, inventory, and pairing output")
        console.print("3. local description: short note shown in node lists and passed to the controller on connect")
    selected = role.strip().lower() if role is not None else _choose_runtime_role()
    if selected == "control":
        selected = "controller"
    elif selected == "client":
        selected = "managed"
    if selected not in runtime_roles():
        raise typer.BadParameter(f"role must be one of: {', '.join(runtime_roles())}")
    selected_name = (name.strip() if name is not None else "") or typer.prompt(
        "Local node name",
        default=_current_node_name() or socket.gethostname(),
    )
    selected_description = (
        description.strip() if description is not None else ""
    ) or typer.prompt(
        "Local description",
        default=current_settings.node_description or "",
        show_default=bool(current_settings.node_description),
    ).strip()
    try:
        updated = set_role(selected)
        previous_name = _current_node_name()
        set_node_name(selected_name)
        set_local_node_description(selected_description)
        local_node = ensure_local_node(
            selected_name,
            updated.role or "managed",
            runtime_hostname=socket.gethostname(),
            description=selected_description,
            previous_name=previous_name,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"[green]Set local node type to {updated.role}[/green]")
    console.print(f"[green]Registered local node name:[/green] {local_node.name}")
    if selected_description:
        console.print(f"[green]Registered local description:[/green] {selected_description}")


@app.command("init")
def init_command(
    role: str | None = typer.Option(None, "--role", help="Optional node type to set directly: controller or managed."),
    name: str | None = typer.Option(None, "--name", help="Optional local node name to register directly."),
    description: str | None = typer.Option(None, "--description", help="Optional short local description."),
) -> None:
    """Initialize this installation by choosing the local role and node name."""
    _run_init(role=role, name=name, description=description)


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
    target: str | None = typer.Argument(None, help="Optional node name."),
    runtime_role: str | None = typer.Argument(None, help="Optional new runtime role."),
) -> None:
    """Edit one node runtime role.

    Without arguments, this lists registered nodes and lets you choose one first.
    """
    selected_target = target or _choose_registered_node()
    selected_role = runtime_role or _choose_runtime_role()
    if selected_target == (_current_node_name() or ""):
        _set_local_role(selected_role)
        return
    _require_role("controller")
    try:
        updated = set_node_runtime_role(selected_target, selected_role)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"[green]Set node role:[/green] {updated.name} -> {updated.runtime_role}")


@node_app.command("edit")
def node_edit_command(
    target: str | None = typer.Argument(None, help="Optional current node name."),
    field: str | None = typer.Argument(None, help="Optional field to edit: name or description."),
    value: str | None = typer.Argument(None, help="Optional new value."),
) -> None:
    """Edit one registered node.

    This command edits node metadata only.
    Without arguments, it lists registered nodes and lets you choose the node,
    field, and new value interactively.
    """
    _require_role("controller")
    selected_target = target or _choose_registered_node()
    selected_field = (field or _pick_from_list("Node field", ("name", "description"))).strip().lower()
    node = find_node(selected_target)
    if node is None:
        raise typer.BadParameter(f"unknown node: {selected_target}")
    default_value = node.name if selected_field == "name" else node.description
    selected_value = (value.strip() if value is not None else "") or typer.prompt(
        "New value",
        default=default_value,
    ).strip()
    if selected_field == "name":
        try:
            renamed = rename_node(node.name, selected_value)
            if _current_node_name() == node.name:
                set_node_name(renamed.name)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        console.print(f"[green]Renamed node:[/green] {node.name} -> {renamed.name}")
        return
    if selected_field == "description":
        from homebase_cli.registry import set_node_description

        try:
            updated = set_node_description(node.name, selected_value)
            if _current_node_name() == node.name:
                set_local_node_description(selected_value)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        console.print(f"[green]Updated node description:[/green] {updated.name}")
        return
    raise typer.BadParameter("node edit field must be one of: name, description")


@node_app.command("remove")
def node_remove_command(
    target: str | None = typer.Argument(None, help="Optional node name to remove."),
) -> None:
    """Remove one registered node from the inventory."""
    _require_role("controller")
    selected_target = target or _choose_registered_node()
    if selected_target == (_current_node_name() or ""):
        raise typer.BadParameter("cannot remove the local controller node")
    try:
        removed = remove_node(selected_target)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"[green]Removed node:[/green] {removed.name}")


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
    group: str | None = typer.Argument(None, help="Optional current group name."),
    field: str | None = typer.Argument(None, help="Optional field to edit: name or description."),
    value: str | None = typer.Argument(None, help="Optional new value."),
) -> None:
    """Edit one group.

    Without arguments, this lists groups and lets you choose the group, field,
    and new value interactively.
    """
    _require_role("controller")
    selected_group = group or _choose_registered_group()
    selected_field = (field or _pick_from_list("Group field", ("name", "description"))).strip().lower()
    current_group = _find_group(selected_group)
    if current_group is None:
        raise typer.BadParameter(f"unknown group: {selected_group}")
    default_value = current_group.name if selected_field == "name" else current_group.description
    selected_value = (value.strip() if value is not None else "") or typer.prompt("New value", default=default_value).strip()
    normalized = selected_field
    try:
        if normalized == "name":
            updated = rename_role_group(selected_group, selected_value)
            console.print(f"[green]Renamed group:[/green] {selected_group} -> {updated.name}")
            return
        if normalized == "description":
            updated = set_role_group_description(selected_group, selected_value)
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


@package_app.command("status")
def package_status_command(
    resources: list[str] = typer.Argument(None, help="Optional node names."),
    group: list[str] = typer.Option(None, "--group", help="Limit to one or more groups."),
    all_nodes: bool = typer.Option(False, "--all", help="Show package status for all registered nodes."),
) -> None:
    """Show the installed homebase revision locally or across selected nodes."""
    selected_nodes = _selected_package_nodes(resources, group, all_nodes) if (resources or group or all_nodes) else []
    if selected_nodes:
        current_name = _current_node_name()
        table = Table(show_header=True, header_style="bold")
        table.add_column("Node")
        table.add_column("Address")
        table.add_column("Installed")
        table.add_column("Requested")
        table.add_column("Resolved")
        table.add_column("Summary")
        table.add_column("Installed At")
        table.add_column("Result")
        def worker(node, stage_callback):
            is_local = bool(current_name and node.name == current_name)
            stage_callback(2, 3, "querying")
            if is_local:
                stage_callback(3, 3, "local status ready")
                return {
                    "payload": _local_package_status_payload(),
                    "address": detect_primary_address() or "",
                    "result": "ok",
                }
            if not node.address:
                stage_callback(3, 3, "no address")
                return {"payload": None, "address": "", "result": "no address"}
            payload = fetch_package_status(node.address, port=node.client_port or DEFAULT_CLIENT_PORT)
            stage_callback(3, 3, "response received" if payload is not None else "no response")
            return {
                "payload": payload,
                "address": node.address,
                "result": "ok" if payload is not None else "no response",
            }

        def row_builder(node, outcome):
            payload = outcome["payload"] or {}
            return (
                _node_label(node.name),
                outcome["address"],
                str(payload.get("installed_version") or "not installed"),
                str(payload.get("requested_ref") or ""),
                str(payload.get("resolved_ref") or ""),
                str(payload.get("summary") or ""),
                str(payload.get("installed_at") or ""),
                outcome["result"],
            )

        for row in _run_package_batch(
            selected_nodes=selected_nodes,
            description_prefix="Checking package status",
            worker=worker,
            row_builder=row_builder,
        ):
            table.add_row(*row)
        console.print(table)
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
    description_prefix: str,
    ref: str,
    repo_url: str,
    python_bin: str | None,
    summary: str | None,
) -> None:
    if should_defer_windows_self_update(python_bin):
        helper_pid, result_path, log_path = schedule_windows_self_update(
            ref,
            repo_url=repo_url,
            python_bin=python_bin,
            summary=summary,
        )
        console.print("[yellow]Windows local self-update started in a background helper.[/yellow]")
        console.print(f"helper pid: {helper_pid}")
        console.print(f"result file: {result_path}")
        console.print(f"log file: {log_path}")
        console.print("Open a new terminal in a few seconds and check `homebase package status`.")
        return
    local_name = _current_node_name() or "local"
    node_logs = {local_name: []}
    stage_state = {local_name: (1, 6, f"resolving target {ref}", "running")}
    with Live(_render_package_panels(selected_nodes=[SimpleNamespace(name=local_name)], node_logs=node_logs, stage_state=stage_state), console=console, refresh_per_second=8) as live:
        last_stage: tuple[int, int, str] | None = None

        def stage_callback(step: int, total: int, label: str, status: str = "running") -> None:
            nonlocal last_stage
            current_stage = (step, total, label, status)
            if current_stage == last_stage:
                return
            last_stage = current_stage
            stage_state[local_name] = (step, total, label, status)
            if _should_persist_package_stage(label=label, status=status):
                node_logs[local_name].append(
                    _package_stage_text(
                        description_prefix=description_prefix,
                        node_name=local_name,
                        step=step,
                        total=total,
                        label=label,
                        status=status,
                    ),
                )
            live.update(_render_package_panels(selected_nodes=[SimpleNamespace(name=local_name)], node_logs=node_logs, stage_state=stage_state), refresh=True)

        try:
            _, status = install_github_ref(
                ref,
                repo_url=repo_url,
                python_bin=python_bin,
                summary=summary,
                on_stage=stage_callback,
            )
        except PackageOperationError as exc:
            stage_state[local_name] = (6, 6, str(exc), "failed")
            node_logs[local_name].append(
                _package_stage_text(
                    description_prefix=description_prefix,
                    node_name=local_name,
                    step=6,
                    total=6,
                    label=str(exc),
                    status="failed",
                )
            )
            live.update(_render_package_panels(selected_nodes=[SimpleNamespace(name=local_name)], node_logs=node_logs, stage_state=stage_state), refresh=True)
            console.print(f"[red]Package install failed.[/red] Log: {exc.log_path}")
            console.print(f"Check the log with: `less {exc.log_path}`")
            raise typer.Exit(code=1)
        stage_state[local_name] = (6, 6, "done", "done")
        done_line = _package_stage_text(
            description_prefix=description_prefix,
            node_name=local_name,
            step=6,
            total=6,
            label="done",
            status="done",
        )
        if not node_logs[local_name] or node_logs[local_name][-1] != done_line:
            node_logs[local_name].append(done_line)
        live.update(_render_package_panels(selected_nodes=[SimpleNamespace(name=local_name)], node_logs=node_logs, stage_state=stage_state), refresh=True)
    console.print(f"[green]Installed version:[/green] {status.installed_version or 'unknown'}")
    console.print(f"[green]Requested ref:[/green] {status.requested_ref}")
    if status.resolved_ref:
        console.print(f"[green]Resolved commit:[/green] {status.resolved_ref}")
    if python_bin is not None:
        console.print(f"[green]Installed into Python:[/green] {python_bin}")


@package_app.command("install")
def package_install_command(
    resources: list[str] = typer.Argument(None, help="Optional node names."),
    ref: str | None = typer.Option(None, "--ref", help="GitHub ref to install: branch, tag, release tag, or commit SHA."),
    repo_url: str = typer.Option(DEFAULT_REPO_URL, "--repo", help="GitHub repository URL."),
    python_bin: str | None = typer.Option(None, "--python", help="Explicit Python executable to install into. Defaults to the current Python environment."),
    include_prerelease: bool = typer.Option(False, "--pre-release", help="Include prerelease GitHub releases when choosing interactively."),
    group: list[str] = typer.Option(None, "--group", help="Install on one or more groups."),
    all_nodes: bool = typer.Option(False, "--all", help="Install on all registered nodes."),
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
    selected_nodes = _selected_package_nodes(resources, group, all_nodes) if (resources or group or all_nodes) else []
    if selected_nodes:
        current_name = _current_node_name()
        table = Table(show_header=True, header_style="bold")
        table.add_column("Node")
        table.add_column("Address")
        table.add_column("Installed")
        table.add_column("Requested")
        table.add_column("Resolved")
        table.add_column("Result")
        def worker(node, stage_callback):
            is_local = bool(current_name and node.name == current_name)
            if is_local:
                if should_defer_windows_self_update(python_bin):
                    helper_pid, result_path, log_path = schedule_windows_self_update(
                        selected_ref,
                        repo_url=repo_url,
                        python_bin=python_bin,
                        summary=selected_summary,
                    )
                    return {
                        "payload": {
                            "installed_version": "scheduled",
                            "requested_ref": selected_ref,
                            "resolved_ref": "",
                        },
                        "address": detect_primary_address() or "",
                        "result": f"background helper pid {helper_pid}; result: {result_path}; log: {log_path}",
                    }
                try:
                    _, status = install_github_ref(
                        selected_ref,
                        repo_url=repo_url,
                        python_bin=python_bin,
                        summary=selected_summary,
                        on_stage=stage_callback,
                    )
                    return {
                        "payload": {
                            "installed_version": status.installed_version,
                            "requested_ref": status.requested_ref,
                            "resolved_ref": status.resolved_ref,
                        },
                        "address": detect_primary_address() or "",
                        "result": "ok",
                    }
                except PackageOperationError as exc:
                    return {"payload": None, "address": detect_primary_address() or "", "result": f"log: {exc.log_path}"}
            if not node.address:
                stage_callback(3, 6, "no address")
                return {"payload": None, "address": "", "result": "no address"}
            job_id = secrets.token_hex(8)
            stage_callback(3, 6, "requesting remote install")
            result_holder: dict[str, object] = {}
            seen_events = 0

            def run_request() -> None:
                result_holder["payload"] = request_package_install(
                    node.address,
                    ref=selected_ref,
                    repo_url=repo_url,
                    summary=selected_summary,
                    job_id=job_id,
                    port=node.client_port or DEFAULT_CLIENT_PORT,
                )

            request_thread = ThreadPoolExecutor(max_workers=1)
            future = request_thread.submit(run_request)
            try:
                while not future.done():
                    progress_payload = fetch_package_progress(
                        node.address,
                        job_id=job_id,
                        port=node.client_port or DEFAULT_CLIENT_PORT,
                    )
                    seen_events = _consume_package_progress_events(
                        progress_payload=progress_payload,
                        seen_events=seen_events,
                        default_step=3,
                        stage_callback=stage_callback,
                    )
                    time.sleep(0.2)
            finally:
                request_thread.shutdown(wait=True)
            progress_payload = fetch_package_progress(
                node.address,
                job_id=job_id,
                port=node.client_port or DEFAULT_CLIENT_PORT,
            )
            seen_events = _consume_package_progress_events(
                progress_payload=progress_payload,
                seen_events=seen_events,
                default_step=3,
                stage_callback=stage_callback,
            )
            payload = result_holder.get("payload")
            return {
                "payload": payload,
                "address": node.address,
                "result": "ok" if payload is not None else "no response",
            }

        def row_builder(node, outcome):
            payload = outcome["payload"] or {}
            return (
                _node_label(node.name),
                outcome["address"],
                str(payload.get("installed_version") or "unknown"),
                str(payload.get("requested_ref") or selected_ref),
                str(payload.get("resolved_ref") or ""),
                outcome["result"],
            )

        for row in _run_package_batch(
            selected_nodes=selected_nodes,
            description_prefix="Installing selected ref",
            worker=worker,
            row_builder=row_builder,
        ):
            table.add_row(*row)
        console.print(table)
        return
    _run_install_flow(
        description_prefix="Installing",
        ref=selected_ref,
        repo_url=repo_url,
        python_bin=python_bin,
        summary=selected_summary,
    )


@package_app.command("update")
def package_update_command(
    resources: list[str] = typer.Argument(None, help="Optional node names."),
    repo_url: str = typer.Option(DEFAULT_REPO_URL, "--repo", help="GitHub repository URL."),
    python_bin: str | None = typer.Option(None, "--python", help="Explicit Python executable to install into. Defaults to the current Python environment."),
    include_prerelease: bool = typer.Option(False, "--pre-release", help="Allow prerelease versions when selecting the latest target."),
    group: list[str] = typer.Option(None, "--group", help="Update one or more groups."),
    all_nodes: bool = typer.Option(False, "--all", help="Update all registered nodes."),
) -> None:
    """Update to the latest available GitHub target."""
    try:
        latest = latest_github_version(repo_url, include_prerelease=include_prerelease)
    except RuntimeError as exc:
        console.print(f"[red]Latest-version lookup failed.[/red] {exc}")
        raise typer.Exit(code=1)
    console.print(f"[bold]Selected latest target:[/bold] {latest.version}")
    console.print(f"summary: {latest.summary}")
    selected_nodes = _selected_package_nodes(resources, group, all_nodes) if (resources or group or all_nodes) else []
    if selected_nodes:
        current_name = _current_node_name()
        table = Table(show_header=True, header_style="bold")
        table.add_column("Node")
        table.add_column("Address")
        table.add_column("Installed")
        table.add_column("Requested")
        table.add_column("Resolved")
        table.add_column("Result")
        def worker(node, stage_callback):
            is_local = bool(current_name and node.name == current_name)
            if is_local:
                if should_defer_windows_self_update(python_bin):
                    helper_pid, result_path, log_path = schedule_windows_self_update(
                        latest.ref,
                        repo_url=repo_url,
                        python_bin=python_bin,
                        summary=latest.summary,
                    )
                    return {
                        "payload": {
                            "installed_version": "scheduled",
                            "requested_ref": latest.ref,
                            "resolved_ref": "",
                        },
                        "address": detect_primary_address() or "",
                        "result": f"background helper pid {helper_pid}; result: {result_path}; log: {log_path}",
                    }
                try:
                    _, status = install_github_ref(
                        latest.ref,
                        repo_url=repo_url,
                        python_bin=python_bin,
                        summary=latest.summary,
                        on_stage=stage_callback,
                    )
                    return {
                        "payload": {
                            "installed_version": status.installed_version,
                            "requested_ref": status.requested_ref,
                            "resolved_ref": status.resolved_ref,
                        },
                        "address": detect_primary_address() or "",
                        "result": "ok",
                    }
                except PackageOperationError as exc:
                    return {"payload": None, "address": detect_primary_address() or "", "result": f"log: {exc.log_path}"}
            if not node.address:
                stage_callback(3, 6, "no address")
                return {"payload": None, "address": "", "result": "no address"}
            job_id = secrets.token_hex(8)
            stage_callback(2, 6, "requesting remote update")
            result_holder: dict[str, object] = {}
            seen_events = 0

            def run_request() -> None:
                result_holder["payload"] = request_package_upgrade(
                    node.address,
                    repo_url=repo_url,
                    include_prerelease=include_prerelease,
                    job_id=job_id,
                    port=node.client_port or DEFAULT_CLIENT_PORT,
                )

            request_thread = ThreadPoolExecutor(max_workers=1)
            future = request_thread.submit(run_request)
            try:
                while not future.done():
                    progress_payload = fetch_package_progress(
                        node.address,
                        job_id=job_id,
                        port=node.client_port or DEFAULT_CLIENT_PORT,
                    )
                    seen_events = _consume_package_progress_events(
                        progress_payload=progress_payload,
                        seen_events=seen_events,
                        default_step=2,
                        stage_callback=stage_callback,
                    )
                    time.sleep(0.2)
            finally:
                request_thread.shutdown(wait=True)
            progress_payload = fetch_package_progress(
                node.address,
                job_id=job_id,
                port=node.client_port or DEFAULT_CLIENT_PORT,
            )
            seen_events = _consume_package_progress_events(
                progress_payload=progress_payload,
                seen_events=seen_events,
                default_step=2,
                stage_callback=stage_callback,
            )
            payload = result_holder.get("payload")
            return {
                "payload": payload,
                "address": node.address,
                "result": "ok" if payload is not None else "no response",
            }

        def row_builder(node, outcome):
            payload = outcome["payload"] or {}
            return (
                _node_label(node.name),
                outcome["address"],
                str(payload.get("installed_version") or "unknown"),
                str(payload.get("requested_ref") or latest.ref),
                str(payload.get("resolved_ref") or ""),
                outcome["result"],
            )

        for row in _run_package_batch(
            selected_nodes=selected_nodes,
            description_prefix="Updating",
            worker=worker,
            row_builder=row_builder,
        ):
            table.add_row(*row)
        console.print(table)
        return
    _run_install_flow(
        description_prefix="Updating",
        ref=latest.ref,
        repo_url=repo_url,
        python_bin=python_bin,
        summary=latest.summary,
    )


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
    runtime_node_app.command("remove")(node_remove_command)
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
        runtime_connect_app.command("remove")(connect_remove_command)
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
    if _current_runtime_role() == "controller":
        runtime_dev_app.command("docs")(docs_command)
        runtime_dev_app.add_typer(ansible_app, name="ansible")
    return runtime_dev_app


def _build_root_app() -> typer.Typer:
    runtime_app = typer.Typer(no_args_is_help=True, help=app.info.help)
    current_role = _current_runtime_role()
    runtime_app.command("init")(init_command)
    runtime_app.command("status")(status_command)
    runtime_app.command("doc")(docs_command)
    runtime_app.command("docs", hidden=True)(docs_alias_command)
    runtime_app.add_typer(_build_connect_app(), name="connect")
    runtime_app.add_typer(daemon_app, name="daemon")
    runtime_app.add_typer(service_app, name="service")
    runtime_app.add_typer(_build_role_app(), name="role")
    runtime_app.add_typer(_build_package_app(), name="package")
    runtime_app.add_typer(_build_dev_app(), name="dev")
    if current_role == "controller":
        runtime_app.add_typer(_build_node_app(), name="node")
        runtime_app.add_typer(_build_group_app(), name="group")
        runtime_app.add_typer(_build_link_app(), name="link")
        runtime_app.add_typer(inventory_app, name="inventory")
    return runtime_app


app = _build_root_app()


def main() -> None:
    """Run the CLI app."""
    try:
        if len(sys.argv) == 1 and _needs_initialization():
            console.print("[yellow]homebase is not initialized yet. Starting init...[/yellow]")
            _run_init()
            return
        app()
    except click.Abort:
        console.print("[yellow]Cancelled.[/yellow]")
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
