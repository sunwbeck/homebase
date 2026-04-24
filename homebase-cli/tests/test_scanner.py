from pathlib import Path
from types import SimpleNamespace

from homebase_cli.client import ClientDiscovery, ClientProfile
from homebase_cli.registry import add_node
from homebase_cli.scanner import (
    DiscoveredNode,
    PairingError,
    detect_scannable_networks,
    fetch_package_status,
    iter_candidate_addresses,
    load_discovered_nodes,
    pair_with_client,
    request_package_install,
    request_package_upgrade,
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
            return ClientDiscovery(node_id="abc123", node_name="app", hostname="app", platform="Linux 6.1", version="0.1.0")
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
            discovery=ClientDiscovery(node_id="abc123", node_name="app", hostname="app", platform="Linux 6.1", version="0.1.0"),
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
                discovery=ClientDiscovery(node_id="abc123", node_name="app", hostname="app", platform="Linux 6.1", version="0.1.0"),
            ),
            DiscoveredNode(
                address="192.168.1.11",
                port=8428,
                discovery=ClientDiscovery(node_id="def456", node_name="db", hostname="db", platform="Linux 6.1", version="0.1.0"),
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
        node_name="app",
        hostname="app",
        platform="Linux 6.1",
        version="0.1.0",
        open_ports=(22,),
        services=("ssh",),
        exposed_endpoints=((22, "ssh", None),),
        endpoint_records=((22, "ssh", None, None),),
        service_records=(("ssh", "running", None, "service", ""),),
    )
    monkeypatch.setattr(
        "homebase_cli.scanner._http_request",
        lambda method, address, path, **kwargs: (200, '{"node_id":"abc123","node_name":"app","hostname":"app","platform":"Linux 6.1","version":"0.1.0","open_ports":[22],"services":["ssh"]}'),
    )
    paired = pair_with_client("192.168.1.10", "12345678")
    assert paired == profile


def test_pair_with_client_uses_longer_timeout(monkeypatch) -> None:
    seen = {}

    def fake_request(method, address, path, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        return (200, '{"node_id":"abc123","node_name":"app","hostname":"app","platform":"Linux 6.1","version":"0.1.0"}')

    monkeypatch.setattr("homebase_cli.scanner._http_request", fake_request)
    pair_with_client("192.168.1.10", "12345678")
    assert seen["timeout"] == 8.0


def test_pair_with_client_raises_remote_error(monkeypatch) -> None:
    monkeypatch.setattr(
        "homebase_cli.scanner._http_request",
        lambda method, address, path, **kwargs: (403, '{"error":"pairing code expired"}'),
    )
    try:
        pair_with_client("192.168.1.10", "12345678")
    except PairingError as exc:
        assert str(exc) == "pairing code expired"
    else:
        raise AssertionError("expected PairingError")


def test_fetch_package_status_returns_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        "homebase_cli.scanner._http_request",
        lambda method, address, path, **kwargs: (200, '{"installed_version":"0.1.1","requested_ref":"v0.1.1"}'),
    )
    payload = fetch_package_status("192.168.1.10")
    assert payload == {"installed_version": "0.1.1", "requested_ref": "v0.1.1"}


def test_request_package_install_returns_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        "homebase_cli.scanner._http_request",
        lambda method, address, path, **kwargs: (200, '{"installed_version":"0.1.1","resolved_ref":"abc123"}'),
    )
    payload = request_package_install("192.168.1.10", ref="v0.1.1", repo_url="https://github.com/sunwbeck/homebase.git")
    assert payload == {"installed_version": "0.1.1", "resolved_ref": "abc123"}


def test_request_package_upgrade_returns_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        "homebase_cli.scanner._http_request",
        lambda method, address, path, **kwargs: (200, '{"installed_version":"0.1.2","resolved_ref":"def456"}'),
    )
    payload = request_package_upgrade("192.168.1.10", repo_url="https://github.com/sunwbeck/homebase.git")
    assert payload == {"installed_version": "0.1.2", "resolved_ref": "def456"}
