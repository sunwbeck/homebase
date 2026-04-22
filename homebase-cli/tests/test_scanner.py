from pathlib import Path
from types import SimpleNamespace

from homebase_cli.client import ClientDiscovery, ClientProfile
from homebase_cli.registry import add_node
from homebase_cli.scanner import (
    DiscoveredNode,
    detect_scannable_networks,
    iter_candidate_addresses,
    load_discovered_nodes,
    pair_with_client,
    save_discovered_nodes,
    scan_for_clients,
    unregistered_discovered_nodes,
)


def test_detect_scannable_networks_returns_tuple(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout="lo UNKNOWN 127.0.0.1/8\neth0 UP 192.168.219.107/24\nwlan0 UP 192.168.219.109/24\ntailscale0 UNKNOWN 100.93.33.36/32\n",
            stderr="",
        )

    monkeypatch.setattr("homebase_cli.scanner.subprocess.run", fake_run)
    networks = detect_scannable_networks()
    assert networks == ("192.168.219.0/24",)


def test_iter_candidate_addresses_expands_ipv4_network() -> None:
    assert iter_candidate_addresses("192.168.1.0/30") == ("192.168.1.1", "192.168.1.2")


def test_scan_for_clients_returns_only_responding_nodes(monkeypatch) -> None:
    monkeypatch.setattr("homebase_cli.scanner.iter_candidate_addresses", lambda cidr: ("192.168.1.10", "192.168.1.11"))

    def fake_fetch_discovery(address: str, port: int, timeout: float):
        if address == "192.168.1.10":
            return ClientDiscovery(node_id="abc123", hostname="app", platform="Linux 6.1", version="0.1.0")
        return None

    monkeypatch.setattr("homebase_cli.scanner.fetch_discovery", fake_fetch_discovery)
    results = scan_for_clients("192.168.1.0/24", workers=1)
    assert len(results) == 1
    assert results[0].address == "192.168.1.10"
    assert results[0].discovery.hostname == "app"


def test_save_and_load_discovered_nodes_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "discovered.json"
    nodes = (
        DiscoveredNode(
            address="192.168.1.10",
            port=8428,
            discovery=ClientDiscovery(node_id="abc123", hostname="app", platform="Linux 6.1", version="0.1.0"),
        ),
    )
    save_discovered_nodes(nodes, path=path)
    loaded = load_discovered_nodes(path)
    assert len(loaded) == 1
    assert loaded[0].discovery.hostname == "app"


def test_unregistered_discovered_nodes_filters_registered_entries(tmp_path: Path, monkeypatch) -> None:
    discovery_path = tmp_path / "discovered.json"
    registry_path = tmp_path / "nodes.toml"
    save_discovered_nodes(
        (
            DiscoveredNode(
                address="192.168.1.10",
                port=8428,
                discovery=ClientDiscovery(node_id="abc123", hostname="app", platform="Linux 6.1", version="0.1.0"),
            ),
            DiscoveredNode(
                address="192.168.1.11",
                port=8428,
                discovery=ClientDiscovery(node_id="def456", hostname="db", platform="Linux 6.1", version="0.1.0"),
            ),
        ),
        path=discovery_path,
    )
    add_node(name="host.app", kind="vm", address="192.168.1.10", node_id="abc123", path=registry_path)
    monkeypatch.setenv("HOMEBASE_DISCOVERY_PATH", str(discovery_path))
    monkeypatch.setenv("HOMEBASE_REGISTRY_PATH", str(registry_path))
    pending = unregistered_discovered_nodes()
    assert [item.address for item in pending] == ["192.168.1.11"]


def test_pair_with_client_returns_profile(monkeypatch) -> None:
    profile = ClientProfile(
        node_id="abc123",
        hostname="app",
        platform="Linux 6.1",
        version="0.1.0",
        open_ports=(22,),
        services=("ssh",),
    )
    monkeypatch.setattr(
        "homebase_cli.scanner._http_request",
        lambda method, address, path, **kwargs: (200, '{"node_id":"abc123","hostname":"app","platform":"Linux 6.1","version":"0.1.0","open_ports":[22],"services":["ssh"]}'),
    )
    paired = pair_with_client("192.168.1.10", "12345678")
    assert paired == profile
