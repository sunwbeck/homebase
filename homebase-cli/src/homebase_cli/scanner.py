"""homebase client discovery and pairing helpers."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
import http.client
import ipaddress
import json
import os
from pathlib import Path
import subprocess

from homebase_cli.client import (
    DEFAULT_CLIENT_PORT,
    DISCOVERY_PATH,
    PACKAGE_INSTALL_PATH,
    PACKAGE_STATUS_PATH,
    PACKAGE_UPGRADE_PATH,
    PAIR_PATH,
    PROFILE_PATH,
    ClientDiscovery,
    ClientProfile,
    PairRequest,
    detect_primary_address,
    local_controller_hostname,
    local_controller_id,
    parse_discovery_payload,
    parse_profile_payload,
)
from homebase_cli.paths import LOCAL_CLI_ROOT
from homebase_cli.registry import load_nodes


DEFAULT_DISCOVERY_PATH = LOCAL_CLI_ROOT / "config" / "discovered_nodes.json"


@dataclass(frozen=True)
class DiscoveredNode:
    """One discovered homebase client on the network."""

    address: str
    discovery: ClientDiscovery
    port: int = DEFAULT_CLIENT_PORT


def discovery_path(path: Path | None = None) -> Path:
    """Resolve the active discovery cache path."""
    if path is not None:
        return path
    override = os.environ.get("HOMEBASE_DISCOVERY_PATH")
    if override:
        return Path(override)
    return DEFAULT_DISCOVERY_PATH


def save_discovered_nodes(nodes: tuple[DiscoveredNode, ...], path: Path | None = None) -> Path:
    """Persist the latest discovery results as JSON."""
    target = discovery_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "address": node.address,
            "port": node.port,
            "discovery": asdict(node.discovery),
        }
        for node in nodes
    ]
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def load_discovered_nodes(path: Path | None = None) -> tuple[DiscoveredNode, ...]:
    """Load cached discovery results."""
    source = discovery_path(path)
    if not source.exists():
        return ()
    raw = json.loads(source.read_text(encoding="utf-8"))
    discovered: list[DiscoveredNode] = []
    for item in raw:
        discovery = parse_discovery_payload(dict(item["discovery"]))
        discovered.append(
            DiscoveredNode(
                address=str(item["address"]),
                port=int(item.get("port", DEFAULT_CLIENT_PORT)),
                discovery=discovery,
            )
        )
    return tuple(sorted(discovered, key=lambda entry: entry.address))


def registered_matches() -> set[str]:
    """Return registered addresses and node ids for quick matching."""
    matched: set[str] = set()
    for node in load_nodes():
        if node.address:
            matched.add(node.address)
        if node.node_id:
            matched.add(node.node_id)
    return matched


def unregistered_discovered_nodes(path: Path | None = None) -> tuple[DiscoveredNode, ...]:
    """Return cached discovered nodes not yet present in the registry."""
    matched = registered_matches()
    pending = [
        node
        for node in load_discovered_nodes(path)
        if node.address not in matched and node.discovery.node_id not in matched
    ]
    return tuple(pending)


def detect_scannable_networks() -> tuple[str, ...]:
    """Return non-loopback IPv4 networks detected on this machine."""
    proc = subprocess.run(
        ["ip", "-4", "-br", "addr"],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "failed to detect interfaces")

    networks: list[str] = []
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        if parts[0] == "lo":
            continue
        for token in parts[2:]:
            try:
                interface = ipaddress.ip_interface(token)
            except ValueError:
                continue
            if interface.network.prefixlen >= 32:
                continue
            cidr = str(interface.network)
            if cidr not in networks:
                networks.append(cidr)
    return tuple(networks)


def iter_candidate_addresses(cidr: str) -> tuple[str, ...]:
    """Expand one IPv4 network into host addresses."""
    network = ipaddress.ip_network(cidr, strict=False)
    if network.version != 4:
        raise ValueError("only IPv4 CIDRs are supported")
    return tuple(str(host) for host in network.hosts())


def _http_request(
    method: str,
    address: str,
    path: str,
    *,
    port: int,
    timeout: float,
    headers: dict[str, str] | None = None,
    body: str | None = None,
) -> tuple[int, str] | None:
    connection = http.client.HTTPConnection(address, port, timeout=timeout)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        payload = response.read().decode("utf-8")
        return response.status, payload
    except (OSError, TimeoutError, http.client.HTTPException):
        return None
    finally:
        connection.close()


def fetch_discovery(address: str, port: int = DEFAULT_CLIENT_PORT, timeout: float = 0.5) -> ClientDiscovery | None:
    """Fetch one homebase client discovery payload over HTTP."""
    result = _http_request("GET", address, DISCOVERY_PATH, port=port, timeout=timeout)
    if result is None:
        return None
    status, body = result
    if status != 200:
        return None
    try:
        return parse_discovery_payload(json.loads(body))
    except (json.JSONDecodeError, ValueError):
        return None


def pair_with_client(
    address: str,
    code: str,
    *,
    port: int = DEFAULT_CLIENT_PORT,
    timeout: float = 1.0,
    controller_id: str | None = None,
) -> ClientProfile | None:
    """Send one pair request and return the full client profile on success."""
    request = PairRequest(
        controller_id=controller_id or local_controller_id(),
        code=code,
        hostname=local_controller_hostname(),
        address=detect_primary_address(),
    )
    payload = json.dumps(asdict(request))
    result = _http_request(
        "POST",
        address,
        PAIR_PATH,
        port=port,
        timeout=timeout,
        headers={"Content-Type": "application/json"},
        body=payload,
    )
    if result is None:
        return None
    status, body = result
    if status != 200:
        return None
    try:
        return parse_profile_payload(json.loads(body))
    except (json.JSONDecodeError, ValueError):
        return None


def fetch_profile(
    address: str,
    *,
    port: int = DEFAULT_CLIENT_PORT,
    timeout: float = 0.5,
    controller_id: str | None = None,
) -> ClientProfile | None:
    """Fetch the full profile for an already paired controller."""
    result = _http_request(
        "GET",
        address,
        PROFILE_PATH,
        port=port,
        timeout=timeout,
        headers={"X-Homebase-Controller": controller_id or local_controller_id()},
    )
    if result is None:
        return None
    status, body = result
    if status != 200:
        return None
    try:
        return parse_profile_payload(json.loads(body))
    except (json.JSONDecodeError, ValueError):
        return None


def fetch_package_status(
    address: str,
    *,
    port: int = DEFAULT_CLIENT_PORT,
    timeout: float = 1.0,
    controller_id: str | None = None,
) -> dict[str, object] | None:
    """Fetch current installed package state from one paired client."""
    result = _http_request(
        "GET",
        address,
        PACKAGE_STATUS_PATH,
        port=port,
        timeout=timeout,
        headers={"X-Homebase-Controller": controller_id or local_controller_id()},
    )
    if result is None:
        return None
    status, body = result
    if status != 200:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def request_package_install(
    address: str,
    *,
    ref: str,
    repo_url: str,
    summary: str | None = None,
    port: int = DEFAULT_CLIENT_PORT,
    timeout: float = 60.0,
    controller_id: str | None = None,
) -> dict[str, object] | None:
    """Ask one paired client to install a specific GitHub ref."""
    payload = {"ref": ref, "repo_url": repo_url}
    if summary:
        payload["summary"] = summary
    result = _http_request(
        "POST",
        address,
        PACKAGE_INSTALL_PATH,
        port=port,
        timeout=timeout,
        headers={
            "Content-Type": "application/json",
            "X-Homebase-Controller": controller_id or local_controller_id(),
        },
        body=json.dumps(payload),
    )
    if result is None:
        return None
    status, body = result
    if status != 200:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def request_package_upgrade(
    address: str,
    *,
    repo_url: str,
    include_prerelease: bool = False,
    port: int = DEFAULT_CLIENT_PORT,
    timeout: float = 60.0,
    controller_id: str | None = None,
) -> dict[str, object] | None:
    """Ask one paired client to upgrade to the latest GitHub target."""
    result = _http_request(
        "POST",
        address,
        PACKAGE_UPGRADE_PATH,
        port=port,
        timeout=timeout,
        headers={
            "Content-Type": "application/json",
            "X-Homebase-Controller": controller_id or local_controller_id(),
        },
        body=json.dumps({"repo_url": repo_url, "include_prerelease": include_prerelease}),
    )
    if result is None:
        return None
    status, body = result
    if status != 200:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def scan_for_clients(
    cidr: str,
    *,
    port: int = DEFAULT_CLIENT_PORT,
    timeout: float = 0.35,
    workers: int = 64,
) -> tuple[DiscoveredNode, ...]:
    """Discover homebase clients on one network by querying the known client port."""
    addresses = iter_candidate_addresses(cidr)

    def probe(address: str) -> DiscoveredNode | None:
        discovery = fetch_discovery(address, port=port, timeout=timeout)
        if discovery is None:
            return None
        return DiscoveredNode(address=address, discovery=discovery, port=port)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        discovered = [item for item in pool.map(probe, addresses) if item is not None]
    return tuple(sorted(discovered, key=lambda item: item.address))
