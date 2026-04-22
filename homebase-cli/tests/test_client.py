from pathlib import Path

from homebase_cli.client import (
    ClientState,
    PairRequest,
    load_client_state,
    pair_controller,
    parse_discovery_payload,
    parse_pair_request,
    parse_profile_payload,
    save_client_state,
    state_path,
)


def test_parse_discovery_payload_validates_required_fields() -> None:
    discovery = parse_discovery_payload(
        {
            "node_id": "abc123",
            "hostname": "app",
            "platform": "Linux 6.1",
            "version": "0.1.0",
        }
    )
    assert discovery.node_id == "abc123"
    assert discovery.hostname == "app"


def test_parse_profile_payload_includes_ports_and_services() -> None:
    profile = parse_profile_payload(
        {
            "node_id": "abc123",
            "hostname": "app",
            "platform": "Linux 6.1",
            "version": "0.1.0",
            "open_ports": [22, 8080],
            "services": ["ssh", "docker"],
        }
    )
    assert profile.open_ports == (22, 8080)
    assert profile.services == ("ssh", "docker")


def test_parse_pair_request_requires_8_digits() -> None:
    request = parse_pair_request({"controller_id": "control", "code": "12345678"})
    assert request == PairRequest(controller_id="control", code="12345678")


def test_pair_controller_rotates_code_on_success(tmp_path: Path) -> None:
    path = tmp_path / "client-state.json"
    save_client_state(ClientState(pair_code="12345678", paired_controllers=()), path)
    assert pair_controller(PairRequest(controller_id="control", code="12345678"), path)
    updated = load_client_state(path)
    assert "control" in updated.paired_controllers
    assert updated.pair_code != "12345678"


def test_state_path_uses_environment_override(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "override-state.json"
    monkeypatch.setenv("HOMEBASE_CLIENT_STATE_PATH", str(target))
    assert state_path() == target
