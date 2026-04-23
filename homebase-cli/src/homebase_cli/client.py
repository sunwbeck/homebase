"""Minimal homebase client identity, pairing, and discovery server."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import platform as platform_module
import re
import secrets
import signal
import shutil
import socket
import subprocess
from typing import Any

from homebase_cli.packaging import DEFAULT_REPO_URL, load_install_state, install_github_ref, latest_github_version
from homebase_cli.paths import LOCAL_CLI_ROOT
from homebase_cli.settings import load_settings


DEFAULT_CLIENT_PORT = 8428
DISCOVERY_PATH = "/discovery"
PAIR_PATH = "/pair"
PROFILE_PATH = "/profile"
HEALTH_PATH = "/health"
PACKAGE_STATUS_PATH = "/package/status"
PACKAGE_INSTALL_PATH = "/package/install"
PACKAGE_UPGRADE_PATH = "/package/upgrade"
SERVICE_START_PATH = "/service/start"
SERVICE_STOP_PATH = "/service/stop"
CLIENT_STATE_PATH = Path.home() / ".config" / "homebase" / "client-state.json"
CONNECT_RUNTIME_PATH = LOCAL_CLI_ROOT / "run" / "connect-server.json"
CONNECT_LOG_PATH = LOCAL_CLI_ROOT / "logs" / "connect-server.log"


@dataclass(frozen=True)
class ClientDiscovery:
    """Minimal identity visible before pairing."""

    node_id: str
    node_name: str
    hostname: str
    platform: str
    version: str
    description: str = ""
    pairing_required: bool = True


@dataclass(frozen=True)
class ClientProfile:
    """Full client profile only available after pairing."""

    node_id: str
    node_name: str
    hostname: str
    platform: str
    version: str
    description: str = ""
    open_ports: tuple[int, ...] = ()
    services: tuple[str, ...] = ()
    exposed_endpoints: tuple[tuple[int, str, str | None], ...] = ()
    endpoint_records: tuple[tuple[int, str, str | None, int | None], ...] = ()
    service_records: tuple[tuple[str, str, int | None, str, str], ...] = ()


@dataclass(frozen=True)
class PairRequest:
    """Pairing request from one homebase controller."""

    controller_id: str
    code: str
    hostname: str | None = None
    address: str | None = None


@dataclass(frozen=True)
class PairedController:
    """One controller paired with this managed node."""

    controller_id: str
    hostname: str | None = None
    address: str | None = None


@dataclass(frozen=True)
class ClientState:
    """Persistent pairing state stored on the client node."""

    pair_code: str
    pair_code_expires_at: str | None = None
    paired_controllers: tuple[PairedController, ...] = ()


@dataclass(frozen=True)
class PackageInstallRequest:
    """Remote package install request from one paired controller."""

    repo_url: str = DEFAULT_REPO_URL
    ref: str = "main"
    include_prerelease: bool = False


@dataclass(frozen=True)
class ConnectRuntime:
    """One persisted managed connect server runtime."""

    pid: int
    host: str
    port: int
    started_at: str
    log_path: str


@dataclass(frozen=True)
class ServiceActionRequest:
    """Remote service start/stop request from one paired controller."""

    service: str


def cli_version() -> str:
    """Return the installed CLI version when available."""
    try:
        return version("homebase-cli")
    except PackageNotFoundError:
        return "0.1.0-dev"


def load_connect_runtime(path: Path | None = None) -> ConnectRuntime | None:
    """Load the persisted managed connect server runtime."""
    target = path or CONNECT_RUNTIME_PATH
    if not target.exists():
        return None
    payload = json.loads(target.read_text(encoding="utf-8"))
    return ConnectRuntime(
        pid=int(payload["pid"]),
        host=str(payload["host"]),
        port=int(payload["port"]),
        started_at=str(payload["started_at"]),
        log_path=str(payload["log_path"]),
    )


def save_connect_runtime(runtime: ConnectRuntime, path: Path | None = None) -> Path:
    """Persist the managed connect server runtime."""
    target = path or CONNECT_RUNTIME_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(asdict(runtime), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def clear_connect_runtime(path: Path | None = None) -> None:
    """Remove the persisted managed connect server runtime when present."""
    target = path or CONNECT_RUNTIME_PATH
    try:
        target.unlink()
    except FileNotFoundError:
        return


def connect_server_running(path: Path | None = None) -> ConnectRuntime | None:
    """Return the runtime when the managed connect server PID is alive."""
    runtime = load_connect_runtime(path)
    if runtime is None:
        return None
    try:
        os.kill(runtime.pid, 0)
    except PermissionError:
        return runtime
    except ProcessLookupError:
        clear_connect_runtime(path)
        return None
    except OSError:
        if Path(f"/proc/{runtime.pid}").exists():
            return runtime
        clear_connect_runtime(path)
        return None
    return runtime


def stop_connect_server(path: Path | None = None) -> ConnectRuntime | None:
    """Stop the background managed connect server when running."""
    runtime = connect_server_running(path)
    if runtime is None:
        return None
    try:
        os.kill(runtime.pid, signal.SIGTERM)
    except OSError:
        clear_connect_runtime(path)
        return None
    clear_connect_runtime(path)
    return runtime


def read_machine_id() -> str | None:
    """Read the local machine-id when present."""
    for path in (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")):
        try:
            value = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            continue
        if value:
            return value
    return None


def local_controller_id() -> str:
    """Return the local controller id used during pairing."""
    hostname = socket.gethostname().strip() or "unknown"
    machine_id = read_machine_id()
    return machine_id if machine_id else hostname


def local_controller_hostname() -> str:
    """Return the local controller hostname."""
    return socket.gethostname().strip() or "unknown"


def detect_primary_address() -> str | None:
    """Return one likely primary local IPv4 address."""
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("1.1.1.1", 80))
            address = probe.getsockname()[0].strip()
        finally:
            probe.close()
        if address and not address.startswith("127."):
            return address
    except OSError:
        pass
    try:
        address = socket.gethostbyname(socket.gethostname()).strip()
    except OSError:
        address = ""
    if address and not address.startswith("127."):
        return address
    for command in (["ip", "-4", "-br", "addr"], ["/usr/sbin/ip", "-4", "-br", "addr"]):
        try:
            proc = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            continue
        if proc.returncode != 0:
            continue
        for line in proc.stdout.splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            iface = parts[0].strip()
            if iface == "lo":
                continue
            for token in parts[2:]:
                candidate, _, _ = token.partition("/")
                candidate = candidate.strip()
                if candidate and not candidate.startswith("127."):
                    return candidate
    return None


def detect_open_ports() -> tuple[int, ...]:
    """Return externally reachable listening TCP ports when available."""
    return tuple(port for port, _, _ in detect_exposed_endpoints())


def describe_port(port: int, owner: str | None = None) -> str:
    """Return one generic label for a listening port without hardcoded service names."""
    try:
        return socket.getservbyport(port, "tcp")
    except OSError:
        pass
    normalized_owner = (owner or "").strip()
    if normalized_owner.endswith(".service"):
        normalized_owner = normalized_owner.removesuffix(".service")
    return normalized_owner or str(port)


def _interface_addresses() -> dict[str, str]:
    """Return one address-to-interface map for current addresses."""
    for command in (["ip", "-br", "addr"], ["/usr/sbin/ip", "-br", "addr"]):
        try:
            proc = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            continue
        if proc.returncode != 0:
            continue
        mapping: dict[str, str] = {}
        for line in proc.stdout.splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            iface = parts[0].strip()
            for token in parts[2:]:
                candidate, _, _ = token.partition("/")
                normalized = candidate.strip().strip("[]").split("%", 1)[0]
                if normalized:
                    mapping[normalized] = iface
        if mapping:
            return mapping
    return {}


def _socket_listing_output() -> str:
    """Return listening socket output, retrying with sudo when process info is hidden."""
    proc = subprocess.run(
        ["ss", "-ltnpH"],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return ""
    stdout = proc.stdout
    if "pid=" in stdout or os.geteuid() == 0:
        return stdout
    sudo = shutil.which("sudo")
    if sudo is None:
        return stdout
    sudo_proc = subprocess.run(
        [sudo, "-n", "ss", "-ltnpH"],
        check=False,
        capture_output=True,
        text=True,
    )
    if sudo_proc.returncode == 0 and "pid=" in sudo_proc.stdout:
        return sudo_proc.stdout
    return stdout


def detect_endpoint_records() -> tuple[tuple[int, str, str | None, int | None], ...]:
    """Return externally reachable listening endpoints as (port, purpose, owner, pid)."""
    stdout = _socket_listing_output()
    if not stdout:
        return ()
    interface_by_address = _interface_addresses()
    endpoints: dict[tuple[int, int | None], tuple[int, str, str | None, int | None]] = {}
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        local_address = parts[3]
        host, _, port_text = local_address.rpartition(":")
        if not host:
            continue
        normalized_host = host.strip().strip("[]").split("%", 1)[0]
        if normalized_host in {"127.0.0.1", "::1", "localhost"} or normalized_host.startswith("127."):
            continue
        try:
            port = int(port_text)
        except ValueError:
            continue
        process_blob = " ".join(parts[5:]).strip()
        owner = None
        pid = None
        for pattern in (r'users:\(\("([^"]+)"', r'\(\("([^"]+)"'):
            owner_match = re.search(pattern, process_blob)
            if owner_match is not None:
                owner = owner_match.group(1).strip()
                break
        pid_match = re.search(r"pid=(\d+)", process_blob)
        if pid_match is not None:
            pid = int(pid_match.group(1))
        if owner is None and process_blob:
            owner = process_blob.strip() or None
        fallback_owner = owner or interface_by_address.get(normalized_host)
        endpoints[(port, pid)] = (port, describe_port(port, fallback_owner), owner, pid)
    return tuple(sorted(endpoints.values(), key=lambda item: (item[0], item[3] or -1)))


def detect_exposed_endpoints() -> tuple[tuple[int, str, str | None], ...]:
    """Return externally reachable listening endpoints as (port, purpose, owner)."""
    deduped: dict[int, tuple[int, str, str | None]] = {}
    for port, purpose, owner, _ in detect_endpoint_records():
        deduped.setdefault(port, (port, purpose, owner))
    return tuple(sorted(deduped.values(), key=lambda item: item[0]))


def detect_exposed_services() -> tuple[str, ...]:
    """Return unique externally reachable service labels."""
    labels: list[str] = []
    for _, purpose, _ in detect_exposed_endpoints():
        if purpose not in labels:
            labels.append(purpose)
    return tuple(labels)


def detect_running_services() -> tuple[str, ...]:
    """Return running systemd service unit names when available."""
    proc = subprocess.run(
        ["systemctl", "list-units", "--type=service", "--state=running", "--plain", "--no-legend", "--no-pager"],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return ()
    services: list[str] = []
    for line in proc.stdout.splitlines():
        parts = line.split()
        if not parts:
            continue
        unit = parts[0].strip()
        if not unit.endswith(".service"):
            continue
        services.append(unit.removesuffix(".service"))
    return tuple(sorted(dict.fromkeys(services)))


def detect_service_records() -> tuple[tuple[str, str, int | None, str, str], ...]:
    """Return generic service records from systemd and docker when available."""
    records: dict[tuple[str, str], tuple[str, str, int | None, str, str]] = {}

    systemctl = shutil.which("systemctl")
    if systemctl is not None:
        proc = subprocess.run(
            [systemctl, "list-units", "--type=service", "--all", "--plain", "--no-legend", "--no-pager"],
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                parts = line.split()
                if len(parts) < 4:
                    continue
                unit = parts[0].strip()
                if not unit.endswith(".service"):
                    continue
                name = unit.removesuffix(".service")
                active = parts[2].strip().lower()
                sub = parts[3].strip().lower()
                state = "running" if active == "active" and sub == "running" else sub or active or "unknown"
                description = " ".join(parts[4:]).strip()
                pid: int | None = None
                show_proc = subprocess.run(
                    [systemctl, "show", unit, "--property=MainPID", "--value"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if show_proc.returncode == 0:
                    pid_text = show_proc.stdout.strip()
                    if pid_text.isdigit() and pid_text != "0":
                        pid = int(pid_text)
                records[("systemd", unit)] = (name, state, pid, "systemd", description)

    docker = shutil.which("docker")
    if docker is not None:
        proc = subprocess.run(
            [docker, "ps", "-a", "--format", "{{.Names}}\t{{.State}}\t{{.Status}}"],
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                parts = line.split("\t")
                if len(parts) < 3:
                    continue
                name = parts[0].strip()
                state = parts[1].strip().lower() or "unknown"
                status = parts[2].strip()
                if name:
                    records[("docker", name)] = (name, state, None, "docker", status)

    return tuple(sorted(records.values(), key=lambda item: (item[3], item[0])))


def local_discovery() -> ClientDiscovery:
    """Build the minimal discovery payload."""
    hostname = socket.gethostname().strip() or "unknown"
    machine_id = read_machine_id()
    node_id = machine_id if machine_id else hostname
    platform_label = f"{platform_module.system()} {platform_module.release()}".strip()
    settings = load_settings()
    node_name = settings.node_name or hostname
    return ClientDiscovery(
        node_id=node_id,
        node_name=node_name,
        hostname=hostname,
        platform=platform_label,
        version=cli_version(),
        description=settings.node_description or "",
    )


def local_profile() -> ClientProfile:
    """Build the full client profile for paired controllers."""
    discovery = local_discovery()
    endpoint_records = detect_endpoint_records()
    exposed_endpoints = tuple((port, purpose, owner) for port, purpose, owner, _ in endpoint_records)
    service_records = detect_service_records()
    return ClientProfile(
        node_id=discovery.node_id,
        node_name=discovery.node_name,
        hostname=discovery.hostname,
        platform=discovery.platform,
        version=discovery.version,
        description=discovery.description,
        open_ports=tuple(port for port, _, _ in exposed_endpoints),
        services=tuple(dict.fromkeys([name for name, state, _, _, _ in service_records if state == "running"])),
        exposed_endpoints=exposed_endpoints,
        endpoint_records=endpoint_records,
        service_records=service_records,
    )


def parse_discovery_payload(payload: dict[str, Any]) -> ClientDiscovery:
    """Validate and normalize one fetched discovery payload."""
    node_id = str(payload.get("node_id", "")).strip()
    node_name = str(payload.get("node_name", "")).strip()
    hostname = str(payload.get("hostname", "")).strip()
    platform = str(payload.get("platform", "")).strip()
    version_value = str(payload.get("version", "")).strip()
    pairing_required = bool(payload.get("pairing_required", True))
    description = str(payload.get("description", "")).strip()
    if not node_id:
        raise ValueError("discovery payload is missing node_id")
    if not hostname:
        raise ValueError("discovery payload is missing hostname")
    if not platform:
        raise ValueError("discovery payload is missing platform")
    if not version_value:
        raise ValueError("discovery payload is missing version")
    return ClientDiscovery(
        node_id=node_id,
        node_name=node_name or hostname,
        hostname=hostname,
        platform=platform,
        version=version_value,
        description=description,
        pairing_required=pairing_required,
    )


def parse_profile_payload(payload: dict[str, Any]) -> ClientProfile:
    """Validate and normalize one fetched full profile payload."""
    discovery = parse_discovery_payload(payload)
    raw_open_ports = payload.get("open_ports", ())
    raw_services = payload.get("services", ())
    raw_exposed_endpoints = payload.get("exposed_endpoints", ())
    raw_endpoint_records = payload.get("endpoint_records", ())
    raw_service_records = payload.get("service_records", ())
    open_ports = tuple(sorted(int(port) for port in raw_open_ports))
    services = tuple(str(service).strip() for service in raw_services if str(service).strip())
    exposed_endpoints: list[tuple[int, str, str | None]] = []
    for item in raw_exposed_endpoints:
        if not isinstance(item, dict):
            continue
        try:
            port_value = int(item.get("port"))
        except (TypeError, ValueError):
            continue
        purpose = str(item.get("purpose", "")).strip() or describe_port(port_value)
        owner_raw = item.get("owner")
        owner = str(owner_raw).strip() if owner_raw not in (None, "") else None
        exposed_endpoints.append((port_value, purpose, owner))
    if not exposed_endpoints and open_ports:
        exposed_endpoints = [(port, describe_port(port), None) for port in open_ports]
    endpoint_records: list[tuple[int, str, str | None, int | None]] = []
    for item in raw_endpoint_records:
        if not isinstance(item, dict):
            continue
        try:
            port_value = int(item.get("port"))
        except (TypeError, ValueError):
            continue
        purpose = str(item.get("purpose", "")).strip() or describe_port(port_value)
        owner_raw = item.get("owner")
        owner = str(owner_raw).strip() if owner_raw not in (None, "") else None
        pid_raw = item.get("pid")
        pid = int(pid_raw) if isinstance(pid_raw, int) or (isinstance(pid_raw, str) and str(pid_raw).isdigit()) else None
        endpoint_records.append((port_value, purpose, owner, pid))
    if not endpoint_records and exposed_endpoints:
        endpoint_records = [(port, purpose, owner, None) for port, purpose, owner in exposed_endpoints]
    service_records: list[tuple[str, str, int | None, str, str]] = []
    for item in raw_service_records:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        state = str(item.get("state", "")).strip() or "unknown"
        kind = str(item.get("kind", "")).strip() or "service"
        description = str(item.get("description", "")).strip()
        pid_raw = item.get("pid")
        pid = int(pid_raw) if isinstance(pid_raw, int) or (isinstance(pid_raw, str) and str(pid_raw).isdigit()) else None
        if name:
            service_records.append((name, state, pid, kind, description))
    if not service_records and services:
        service_records = [(service, "running", None, "service", "") for service in services]
    return ClientProfile(
        node_id=discovery.node_id,
        node_name=discovery.node_name,
        hostname=discovery.hostname,
        platform=discovery.platform,
        version=discovery.version,
        description=discovery.description,
        open_ports=open_ports,
        services=services,
        exposed_endpoints=tuple(sorted(exposed_endpoints, key=lambda item: item[0])),
        endpoint_records=tuple(sorted(endpoint_records, key=lambda item: (item[0], item[3] or -1))),
        service_records=tuple(sorted(service_records, key=lambda item: (item[3], item[0]))),
    )


def parse_pair_request(payload: dict[str, Any]) -> PairRequest:
    """Validate and normalize one pair request."""
    controller_id = str(payload.get("controller_id", "")).strip()
    code = normalize_pair_code(str(payload.get("code", "")).strip())
    hostname = str(payload.get("hostname", "")).strip() or None
    address = str(payload.get("address", "")).strip() or None
    if not controller_id:
        raise ValueError("pair request is missing controller_id")
    if len(code) != 8 or not code.isdigit():
        raise ValueError("pair request code must be exactly 8 digits")
    return PairRequest(controller_id=controller_id, code=code, hostname=hostname, address=address)


def state_path(path: Path | None = None) -> Path:
    """Resolve the persistent client state path."""
    if path is not None:
        return path
    override = os.environ.get("HOMEBASE_CLIENT_STATE_PATH")
    if override:
        return Path(override)
    return CLIENT_STATE_PATH


def generate_pair_code() -> str:
    """Return a fresh 8-digit numeric pairing code."""
    return f"{secrets.randbelow(100_000_000):08d}"


def normalize_pair_code(value: str) -> str:
    """Normalize one pairing code by removing whitespace."""
    return "".join(value.split())


def _fresh_pair_code_expiry() -> str:
    """Return the expiry timestamp for one newly issued pairing code."""
    return (datetime.now(UTC) + timedelta(minutes=5)).replace(microsecond=0).isoformat()


def _pair_code_is_expired(expires_at: str | None) -> bool:
    """Return whether one pairing code expiry is missing or in the past."""
    if not expires_at:
        return True
    try:
        expiry = datetime.fromisoformat(expires_at)
    except ValueError:
        return True
    return expiry <= datetime.now(UTC)


def load_client_state(path: Path | None = None) -> ClientState:
    """Load or initialize the persistent client state."""
    target = state_path(path)
    if not target.exists():
        state = ClientState(
            pair_code=generate_pair_code(),
            pair_code_expires_at=_fresh_pair_code_expiry(),
            paired_controllers=(),
        )
        save_client_state(state, target)
        return state
    payload = json.loads(target.read_text(encoding="utf-8"))
    pair_code = str(payload.get("pair_code", "")).strip()
    pair_code_expires_at = payload.get("pair_code_expires_at")
    if pair_code_expires_at not in (None, ""):
        pair_code_expires_at = str(pair_code_expires_at).strip()
    else:
        pair_code_expires_at = None
    paired_entries: list[PairedController] = []
    for item in payload.get("paired_controllers", []):
        if isinstance(item, str):
            controller_id = item.strip()
            if controller_id:
                paired_entries.append(PairedController(controller_id=controller_id))
            continue
        if isinstance(item, dict):
            controller_id = str(item.get("controller_id", "")).strip()
            if controller_id:
                hostname_value = item.get("hostname")
                address_value = item.get("address")
                paired_entries.append(
                    PairedController(
                        controller_id=controller_id,
                        hostname=str(hostname_value).strip() if hostname_value not in (None, "") else None,
                        address=str(address_value).strip() if address_value not in (None, "") else None,
                    )
                )
    paired = tuple(paired_entries)
    if len(pair_code) != 8 or not pair_code.isdigit() or _pair_code_is_expired(pair_code_expires_at):
        pair_code = generate_pair_code()
        pair_code_expires_at = _fresh_pair_code_expiry()
        save_client_state(
            ClientState(
                pair_code=pair_code,
                pair_code_expires_at=pair_code_expires_at,
                paired_controllers=paired,
            ),
            target,
        )
    return ClientState(pair_code=pair_code, pair_code_expires_at=pair_code_expires_at, paired_controllers=paired)


def save_client_state(state: ClientState, path: Path | None = None) -> Path:
    """Persist the client state to disk."""
    target = state_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    expires_at = state.pair_code_expires_at
    if len(state.pair_code) == 8 and state.pair_code.isdigit() and _pair_code_is_expired(expires_at):
        expires_at = _fresh_pair_code_expiry()
    payload = {
        "pair_code": state.pair_code,
        "pair_code_expires_at": expires_at,
        "paired_controllers": [asdict(item) for item in state.paired_controllers],
        "updated_at": datetime.now(UTC).isoformat(),
    }
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def refresh_pair_code(path: Path | None = None) -> ClientState:
    """Generate a new local pairing code while keeping existing pairings."""
    current = load_client_state(path)
    updated = ClientState(
        pair_code=generate_pair_code(),
        pair_code_expires_at=_fresh_pair_code_expiry(),
        paired_controllers=current.paired_controllers,
    )
    save_client_state(updated, path)
    return updated


def is_paired(controller_id: str, path: Path | None = None) -> bool:
    """Return True when this controller id is already paired."""
    return any(item.controller_id == controller_id for item in load_client_state(path).paired_controllers)


def pair_controller(request: PairRequest, path: Path | None = None) -> bool:
    """Attempt to pair one controller using the current local code."""
    current = load_client_state(path)
    if _pair_code_is_expired(current.pair_code_expires_at) or request.code != current.pair_code:
        return False
    paired_by_id = {item.controller_id: item for item in current.paired_controllers}
    paired_by_id[request.controller_id] = PairedController(
        controller_id=request.controller_id,
        hostname=request.hostname,
        address=request.address,
    )
    paired = tuple(sorted(paired_by_id.values(), key=lambda item: item.controller_id))
    updated = ClientState(
        pair_code=generate_pair_code(),
        pair_code_expires_at=_fresh_pair_code_expiry(),
        paired_controllers=paired,
    )
    save_client_state(updated, path)
    return True


def discovery_payload() -> dict[str, Any]:
    """Return the current minimal discovery payload."""
    return asdict(local_discovery())


def profile_payload() -> dict[str, Any]:
    """Return the current full profile payload."""
    profile = local_profile()
    payload = asdict(profile)
    payload["endpoint_records"] = [
        {
            "port": port,
            "purpose": purpose,
            "owner": owner,
            "pid": pid,
        }
        for port, purpose, owner, pid in profile.endpoint_records
    ]
    payload["service_records"] = [
        {
            "name": name,
            "state": state,
            "pid": pid,
            "kind": kind,
            "description": description,
        }
        for name, state, pid, kind, description in profile.service_records
    ]
    return payload


def package_status_payload() -> dict[str, Any]:
    """Return the current installed homebase package state."""
    status = load_install_state()
    return {
        "installed_version": status.installed_version,
        "repo_url": status.repo_url,
        "requested_ref": status.requested_ref,
        "resolved_ref": status.resolved_ref,
        "summary": status.summary,
        "installed_at": status.installed_at,
    }


def parse_service_action_request(payload: dict[str, Any]) -> ServiceActionRequest:
    """Validate one remote service action request."""
    service = str(payload.get("service", "")).strip()
    if not service:
        raise ValueError("service action request is missing service")
    return ServiceActionRequest(service=service)


def control_service(service: str, action: str) -> None:
    """Start or stop one service using generic systemd/docker backends."""
    normalized_action = action.strip().lower()
    if normalized_action not in {"start", "stop"}:
        raise ValueError(f"unsupported service action: {action}")
    target = service.strip()
    if not target:
        raise ValueError("service name cannot be empty")

    systemctl = shutil.which("systemctl")
    if systemctl is not None:
        unit = target if target.endswith(".service") else f"{target}.service"
        probe = subprocess.run(
            [systemctl, "status", unit],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        if probe.returncode in {0, 3, 4}:
            result = subprocess.run([systemctl, normalized_action, unit], check=False, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"failed to {normalized_action} {unit}")
            return

    docker = shutil.which("docker")
    if docker is not None:
        probe = subprocess.run(
            [docker, "inspect", target],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        if probe.returncode == 0:
            result = subprocess.run([docker, normalized_action, target], check=False, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"failed to {normalized_action} {target}")
            return

    raise RuntimeError(f"unknown service target: {target}")


def parse_package_install_request(payload: dict[str, Any]) -> PackageInstallRequest:
    """Validate and normalize one remote package install request."""
    repo_url = str(payload.get("repo_url", DEFAULT_REPO_URL)).strip() or DEFAULT_REPO_URL
    ref = str(payload.get("ref", "")).strip()
    include_prerelease = bool(payload.get("include_prerelease", False))
    if not ref:
        raise ValueError("package install request is missing ref")
    return PackageInstallRequest(repo_url=repo_url, ref=ref, include_prerelease=include_prerelease)


def _require_paired_controller(headers: Any) -> str | None:
    """Return one paired controller id from the request headers."""
    controller_id = headers.get("X-Homebase-Controller", "").strip()
    if not controller_id or not is_paired(controller_id):
        return None
    return controller_id


def make_handler() -> type[BaseHTTPRequestHandler]:
    """Create the request handler class for the client server."""

    class ClientHandler(BaseHTTPRequestHandler):
        def _send_json(self, payload: dict[str, Any], status: int = HTTPStatus.OK) -> None:
            body = json.dumps(payload, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == HEALTH_PATH:
                body = b"ok\n"
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == DISCOVERY_PATH:
                self._send_json(discovery_payload())
                return
            if self.path == PROFILE_PATH:
                if _require_paired_controller(self.headers) is None:
                    self._send_json({"error": "not paired"}, status=HTTPStatus.FORBIDDEN)
                    return
                self._send_json(profile_payload())
                return
            if self.path == PACKAGE_STATUS_PATH:
                if _require_paired_controller(self.headers) is None:
                    self._send_json({"error": "not paired"}, status=HTTPStatus.FORBIDDEN)
                    return
                self._send_json(package_status_payload())
                return
            self.send_error(HTTPStatus.NOT_FOUND, "not found")

        def do_POST(self) -> None:  # noqa: N802
            if self.path == PAIR_PATH:
                content_length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(content_length).decode("utf-8") if content_length else "{}"
                try:
                    request = parse_pair_request(json.loads(body))
                except (json.JSONDecodeError, ValueError) as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                    return
                if not pair_controller(request):
                    self._send_json({"error": "pairing code mismatch"}, status=HTTPStatus.FORBIDDEN)
                    return
                self._send_json(profile_payload())
                return
            if self.path in {SERVICE_START_PATH, SERVICE_STOP_PATH}:
                if _require_paired_controller(self.headers) is None:
                    self._send_json({"error": "not paired"}, status=HTTPStatus.FORBIDDEN)
                    return
                content_length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(content_length).decode("utf-8") if content_length else "{}"
                try:
                    request = parse_service_action_request(json.loads(body) if body else {})
                    action = "start" if self.path == SERVICE_START_PATH else "stop"
                    control_service(request.service, action)
                    self._send_json(profile_payload())
                except (json.JSONDecodeError, ValueError) as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                except Exception as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            if self.path not in {PACKAGE_INSTALL_PATH, PACKAGE_UPGRADE_PATH}:
                self.send_error(HTTPStatus.NOT_FOUND, "not found")
                return
            if _require_paired_controller(self.headers) is None:
                self._send_json({"error": "not paired"}, status=HTTPStatus.FORBIDDEN)
                return
            content_length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(content_length).decode("utf-8") if content_length else "{}"
            payload = json.loads(body) if body else {}
            try:
                if self.path == PACKAGE_UPGRADE_PATH:
                    repo_url = str(payload.get("repo_url", DEFAULT_REPO_URL)).strip() or DEFAULT_REPO_URL
                    include_prerelease = bool(payload.get("include_prerelease", False))
                    latest = latest_github_version(repo_url, include_prerelease=include_prerelease)
                    request = PackageInstallRequest(
                        repo_url=repo_url,
                        ref=latest.ref,
                        include_prerelease=include_prerelease,
                    )
                    summary = latest.summary
                else:
                    request = parse_package_install_request(payload)
                    summary = str(payload.get("summary", "")).strip() or None
            except (json.JSONDecodeError, ValueError, RuntimeError) as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                _, status = install_github_ref(
                    request.ref,
                    repo_url=request.repo_url,
                    summary=summary,
                )
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self._send_json(
                {
                    "installed_version": status.installed_version,
                    "repo_url": status.repo_url,
                    "requested_ref": status.requested_ref,
                    "resolved_ref": status.resolved_ref,
                    "summary": status.summary,
                    "installed_at": status.installed_at,
                }
            )

        def log_message(self, format: str, *args: object) -> None:
            return

    return ClientHandler


def serve_client(host: str = "0.0.0.0", port: int = DEFAULT_CLIENT_PORT) -> None:
    """Start the homebase client identity server."""
    load_client_state()
    server = ThreadingHTTPServer((host, port), make_handler())
    server.serve_forever()
