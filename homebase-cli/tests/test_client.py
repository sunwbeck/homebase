from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

from homebase_cli.client import (
    ClientState,
    PackageInstallRequest,
    PairRequest,
    control_service,
    detect_endpoint_records,
    detect_exposed_endpoints,
    detect_primary_address,
    detect_service_records,
    load_client_state,
    pair_controller,
    parse_package_install_request,
    parse_discovery_payload,
    parse_pair_request,
    parse_profile_payload,
    normalize_pair_code,
    save_client_state,
    state_path,
)


def test_parse_discovery_payload_validates_required_fields() -> None:
    discovery = parse_discovery_payload(
        {
            "node_id": "abc123",
            "node_name": "app",
            "hostname": "app",
            "platform": "Linux 6.1",
            "version": "0.1.0",
        }
    )
    assert discovery.node_id == "abc123"
    assert discovery.node_name == "app"
    assert discovery.hostname == "app"


def test_parse_profile_payload_includes_ports_and_services() -> None:
    profile = parse_profile_payload(
        {
            "node_id": "abc123",
            "node_name": "app",
            "hostname": "app",
            "platform": "Linux 6.1",
            "version": "0.1.0",
            "open_ports": [22, 8080],
            "services": ["ssh", "docker"],
        }
    )
    assert profile.open_ports == (22, 8080)
    assert profile.node_name == "app"
    assert profile.services == ("ssh", "docker")


def test_parse_pair_request_requires_8_digits() -> None:
    request = parse_pair_request({"controller_id": "control", "code": "12345678"})
    assert request == PairRequest(controller_id="control", code="12345678")


def test_parse_pair_request_accepts_spaced_code() -> None:
    request = parse_pair_request({"controller_id": "control", "code": "1234 5678"})
    assert request == PairRequest(controller_id="control", code="12345678")


def test_normalize_pair_code_removes_whitespace() -> None:
    assert normalize_pair_code(" 1234  5678 ") == "12345678"


def test_pair_controller_rotates_code_on_success(tmp_path: Path) -> None:
    path = tmp_path / "client-state.json"
    save_client_state(ClientState(pair_code="12345678", paired_controllers=()), path)
    assert pair_controller(PairRequest(controller_id="control", code="12345678"), path)
    updated = load_client_state(path)
    assert updated.paired_controllers[0].controller_id == "control"
    assert updated.pair_code != "12345678"
    assert updated.pair_code_expires_at is not None


def test_load_client_state_refreshes_expired_code(tmp_path: Path) -> None:
    path = tmp_path / "client-state.json"
    path.write_text(
        '{"pair_code":"12345678","pair_code_expires_at":"2000-01-01T00:00:00+00:00","paired_controllers":[]}\n',
        encoding="utf-8",
    )
    updated = load_client_state(path)
    assert updated.pair_code != "12345678"
    assert updated.pair_code_expires_at is not None


def test_pair_controller_rejects_expired_code(tmp_path: Path) -> None:
    path = tmp_path / "client-state.json"
    path.write_text(
        json.dumps(
            {
                "pair_code": "12345678",
                "pair_code_expires_at": (datetime.now(UTC) - timedelta(minutes=1)).replace(microsecond=0).isoformat(),
                "paired_controllers": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert not pair_controller(PairRequest(controller_id="control", code="12345678"), path)


def test_parse_package_install_request_requires_ref() -> None:
    request = parse_package_install_request({"repo_url": "https://github.com/sunwbeck/homebase.git", "ref": "v0.1.1"})
    assert request == PackageInstallRequest(
        repo_url="https://github.com/sunwbeck/homebase.git",
        ref="v0.1.1",
        include_prerelease=False,
    )


def test_state_path_uses_environment_override(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "override-state.json"
    monkeypatch.setenv("HOMEBASE_CLIENT_STATE_PATH", str(target))
    assert state_path() == target


def test_detect_exposed_endpoints_falls_back_to_interface_name_when_owner_hidden(monkeypatch) -> None:
    def fake_run(args, **kwargs):
        if args[:2] == ["ss", "-ltnpH"]:
            return SimpleNamespace(
                returncode=0,
                stdout="LISTEN 0 4096 100.93.33.36:38339 0.0.0.0:*\n",
                stderr="",
            )
        return SimpleNamespace(
            returncode=0,
            stdout="tailscale0 UP 100.93.33.36/32 fd7a:115c:a1e0::b237:2124/128\n",
            stderr="",
        )

    monkeypatch.setattr("homebase_cli.client.subprocess.run", fake_run)
    endpoints = detect_exposed_endpoints()
    assert endpoints == ((38339, "tailscale0", None),)


def test_detect_endpoint_records_uses_sudo_when_plain_ss_hides_processes(monkeypatch) -> None:
    outputs = iter(
        [
            SimpleNamespace(
                returncode=0,
                stdout="LISTEN 0 4096 100.93.33.36:38339 0.0.0.0:*\n",
                stderr="",
            ),
            SimpleNamespace(
                returncode=0,
                stdout='LISTEN 0 4096 100.93.33.36:38339 0.0.0.0:* users:(("tailscaled",pid=30787,fd=23))\n',
                stderr="",
            ),
            SimpleNamespace(
                returncode=0,
                stdout="tailscale0 UP 100.93.33.36/32 fd7a:115c:a1e0::b237:2124/128\n",
                stderr="",
            ),
        ]
    )

    monkeypatch.setattr("homebase_cli.client.os.geteuid", lambda: 1000)
    monkeypatch.setattr("homebase_cli.client.shutil.which", lambda name: "/usr/bin/sudo" if name == "sudo" else None)
    monkeypatch.setattr("homebase_cli.client.subprocess.run", lambda *args, **kwargs: next(outputs))
    endpoints = detect_endpoint_records()
    assert endpoints == ((38339, "tailscaled", "tailscaled", 30787),)


def test_detect_primary_address_uses_windows_powershell(monkeypatch) -> None:
    monkeypatch.setattr("homebase_cli.client.platform_module.system", lambda: "Windows")
    monkeypatch.setattr(
        "homebase_cli.client.socket.gethostbyname",
        lambda _: "127.0.0.1",
    )
    monkeypatch.setattr(
        "homebase_cli.client._run_powershell",
        lambda script: SimpleNamespace(returncode=0, stdout="192.168.0.50\n", stderr=""),
    )
    assert detect_primary_address() == "192.168.0.50"


def test_detect_endpoint_records_uses_windows_powershell(monkeypatch) -> None:
    monkeypatch.setattr("homebase_cli.client.platform_module.system", lambda: "Windows")
    monkeypatch.setattr(
        "homebase_cli.client._run_powershell",
        lambda script: SimpleNamespace(
            returncode=0,
            stdout='[{"LocalAddress":"192.168.0.50","LocalPort":8428,"OwningProcess":4321,"ProcessName":"python"},{"LocalAddress":"127.0.0.1","LocalPort":9000,"OwningProcess":5000,"ProcessName":"local"}]',
            stderr="",
        ),
    )
    monkeypatch.setattr("homebase_cli.client._interface_addresses", lambda: {})
    endpoints = detect_endpoint_records()
    assert endpoints == ((8428, "python", "python", 4321),)


def test_detect_service_records_uses_windows_services(monkeypatch) -> None:
    monkeypatch.setattr("homebase_cli.client.platform_module.system", lambda: "Windows")
    monkeypatch.setattr(
        "homebase_cli.client._run_powershell",
        lambda script: SimpleNamespace(
            returncode=0,
            stdout='[{"Name":"sshd","State":"Running","ProcessId":1010,"DisplayName":"OpenSSH SSH Server"},{"Name":"WSearch","State":"Stopped","ProcessId":0,"DisplayName":"Windows Search"}]',
            stderr="",
        ),
    )
    records = detect_service_records()
    assert records == (
        ("WSearch", "stopped", None, "windows-service", "Windows Search"),
        ("sshd", "running", 1010, "windows-service", "OpenSSH SSH Server"),
    )


def test_control_service_uses_windows_service_backend(monkeypatch) -> None:
    monkeypatch.setattr("homebase_cli.client.platform_module.system", lambda: "Windows")
    calls: list[str] = []

    def fake_run_powershell(script: str):
        calls.append(script)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("homebase_cli.client._run_powershell", fake_run_powershell)
    control_service("sshd", "start")
    assert calls
    assert "Get-Service -Name 'sshd'" in calls[0]
    assert "Start-Service -Name 'sshd'" in calls[0]
