"""Minimal homebase client identity, pairing, and discovery server."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
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
import socket
import subprocess
from typing import Any

from homebase_cli.packaging import DEFAULT_REPO_URL, load_install_state, install_github_ref, latest_github_version
from homebase_cli.paths import LOCAL_CLI_ROOT


DEFAULT_CLIENT_PORT = 8428
DISCOVERY_PATH = "/discovery"
PAIR_PATH = "/pair"
PROFILE_PATH = "/profile"
HEALTH_PATH = "/health"
PACKAGE_STATUS_PATH = "/package/status"
PACKAGE_INSTALL_PATH = "/package/install"
PACKAGE_UPGRADE_PATH = "/package/upgrade"
CLIENT_STATE_PATH = Path.home() / ".config" / "homebase" / "client-state.json"
CONNECT_RUNTIME_PATH = LOCAL_CLI_ROOT / "run" / "connect-server.json"
CONNECT_LOG_PATH = LOCAL_CLI_ROOT / "logs" / "connect-server.log"
KNOWN_PORT_PURPOSES = {
    22: "ssh",
    53: "dns",
    80: "http",
    111: "rpcbind",
    139: "netbios",
    443: "https",
    445: "smb",
    2049: "nfs",
    3000: "grafana",
    32400: "plex",
    8428: "homebase",
    9090: "prometheus",
}


@dataclass(frozen=True)
class ClientDiscovery:
    """Minimal identity visible before pairing."""

    node_id: str
    hostname: str
    platform: str
    version: str
    pairing_required: bool = True


@dataclass(frozen=True)
class ClientProfile:
    """Full client profile only available after pairing."""

    node_id: str
    hostname: str
    platform: str
    version: str
    open_ports: tuple[int, ...] = ()
    services: tuple[str, ...] = ()


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
    """Return one short human-friendly purpose label for a port."""
    known = KNOWN_PORT_PURPOSES.get(port)
    if known:
        return known
    normalized_owner = (owner or "").strip()
    if normalized_owner.endswith(".service"):
        normalized_owner = normalized_owner.removesuffix(".service")
    return normalized_owner or str(port)


def detect_exposed_endpoints() -> tuple[tuple[int, str, str | None], ...]:
    """Return externally reachable listening endpoints as (port, purpose, owner)."""
    proc = subprocess.run(
        ["ss", "-ltnpH"],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return ()
    endpoints: dict[int, tuple[int, str, str | None]] = {}
    for line in proc.stdout.splitlines():
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
        owner_match = re.search(r'\(\("([^"]+)"', process_blob)
        owner = owner_match.group(1).strip() if owner_match is not None else None
        endpoints[port] = (port, describe_port(port, owner), owner)
    return tuple(sorted(endpoints.values(), key=lambda item: item[0]))


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


def local_discovery() -> ClientDiscovery:
    """Build the minimal discovery payload."""
    hostname = socket.gethostname().strip() or "unknown"
    machine_id = read_machine_id()
    node_id = machine_id if machine_id else hostname
    platform_label = f"{platform_module.system()} {platform_module.release()}".strip()
    return ClientDiscovery(
        node_id=node_id,
        hostname=hostname,
        platform=platform_label,
        version=cli_version(),
    )


def local_profile() -> ClientProfile:
    """Build the full client profile for paired controllers."""
    discovery = local_discovery()
    return ClientProfile(
        node_id=discovery.node_id,
        hostname=discovery.hostname,
        platform=discovery.platform,
        version=discovery.version,
        open_ports=detect_open_ports(),
        services=detect_exposed_services(),
    )


def parse_discovery_payload(payload: dict[str, Any]) -> ClientDiscovery:
    """Validate and normalize one fetched discovery payload."""
    node_id = str(payload.get("node_id", "")).strip()
    hostname = str(payload.get("hostname", "")).strip()
    platform = str(payload.get("platform", "")).strip()
    version_value = str(payload.get("version", "")).strip()
    pairing_required = bool(payload.get("pairing_required", True))
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
        hostname=hostname,
        platform=platform,
        version=version_value,
        pairing_required=pairing_required,
    )


def parse_profile_payload(payload: dict[str, Any]) -> ClientProfile:
    """Validate and normalize one fetched full profile payload."""
    discovery = parse_discovery_payload(payload)
    raw_open_ports = payload.get("open_ports", ())
    raw_services = payload.get("services", ())
    open_ports = tuple(sorted(int(port) for port in raw_open_ports))
    services = tuple(str(service).strip() for service in raw_services if str(service).strip())
    return ClientProfile(
        node_id=discovery.node_id,
        hostname=discovery.hostname,
        platform=discovery.platform,
        version=discovery.version,
        open_ports=open_ports,
        services=services,
    )


def parse_pair_request(payload: dict[str, Any]) -> PairRequest:
    """Validate and normalize one pair request."""
    controller_id = str(payload.get("controller_id", "")).strip()
    code = str(payload.get("code", "")).strip()
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


def load_client_state(path: Path | None = None) -> ClientState:
    """Load or initialize the persistent client state."""
    target = state_path(path)
    if not target.exists():
        state = ClientState(pair_code=generate_pair_code(), paired_controllers=())
        save_client_state(state, target)
        return state
    payload = json.loads(target.read_text(encoding="utf-8"))
    pair_code = str(payload.get("pair_code", "")).strip()
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
    if len(pair_code) != 8 or not pair_code.isdigit():
        pair_code = generate_pair_code()
        save_client_state(ClientState(pair_code=pair_code, paired_controllers=paired), target)
    return ClientState(pair_code=pair_code, paired_controllers=paired)


def save_client_state(state: ClientState, path: Path | None = None) -> Path:
    """Persist the client state to disk."""
    target = state_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pair_code": state.pair_code,
        "paired_controllers": [asdict(item) for item in state.paired_controllers],
        "updated_at": datetime.now(UTC).isoformat(),
    }
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def refresh_pair_code(path: Path | None = None) -> ClientState:
    """Generate a new local pairing code while keeping existing pairings."""
    current = load_client_state(path)
    updated = ClientState(pair_code=generate_pair_code(), paired_controllers=current.paired_controllers)
    save_client_state(updated, path)
    return updated


def is_paired(controller_id: str, path: Path | None = None) -> bool:
    """Return True when this controller id is already paired."""
    return any(item.controller_id == controller_id for item in load_client_state(path).paired_controllers)


def pair_controller(request: PairRequest, path: Path | None = None) -> bool:
    """Attempt to pair one controller using the current local code."""
    current = load_client_state(path)
    if request.code != current.pair_code:
        return False
    paired_by_id = {item.controller_id: item for item in current.paired_controllers}
    paired_by_id[request.controller_id] = PairedController(
        controller_id=request.controller_id,
        hostname=request.hostname,
        address=request.address,
    )
    paired = tuple(sorted(paired_by_id.values(), key=lambda item: item.controller_id))
    updated = ClientState(pair_code=generate_pair_code(), paired_controllers=paired)
    save_client_state(updated, path)
    return True


def discovery_payload() -> dict[str, Any]:
    """Return the current minimal discovery payload."""
    return asdict(local_discovery())


def profile_payload() -> dict[str, Any]:
    """Return the current full profile payload."""
    return asdict(local_profile())


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
