from datetime import UTC, datetime, timedelta
import io
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

from homebase_cli.client import (
    ClientState,
    PAIR_PATH,
    PROFILE_PATH,
    PackageInstallRequest,
    PairRequest,
    control_service,
    detect_endpoint_records,
    detect_exposed_endpoints,
    detect_primary_address,
    detect_service_records,
    load_client_state,
    load_package_job_state,
    pair_controller,
    parse_package_install_request,
    parse_discovery_payload,
    parse_pair_request,
    parse_profile_payload,
    paired_profile_payload,
    make_handler,
    normalize_pair_code,
    pairing_rejection_reason,
    save_connect_runtime,
    save_package_job_state,
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


def test_parse_profile_payload_accepts_legacy_tuple_endpoints() -> None:
    profile = parse_profile_payload(
        {
            "node_id": "abc123",
            "node_name": "app",
            "hostname": "app",
            "platform": "Linux 6.1",
            "version": "0.1.0",
            "open_ports": [8428],
            "exposed_endpoints": [[8428, "homebase", "python"]],
            "endpoint_records": [[8428, "homebase", "python", 1010]],
            "service_records": [["homebase", "running", 1010, "service", "homebase daemon"]],
        }
    )
    assert profile.exposed_endpoints == ((8428, "homebase", "python"),)
    assert profile.endpoint_records == ((8428, "homebase", "python", 1010),)
    assert profile.service_records == (("homebase", "running", 1010, "service", "homebase daemon"),)


def test_paired_profile_payload_is_lightweight(monkeypatch) -> None:
    monkeypatch.setattr(
        "homebase_cli.client.local_discovery",
        lambda: SimpleNamespace(
            node_id="abc123",
            node_name="workstation",
            hostname="DESKTOP-SB",
            platform="Windows 11",
            version="0.1.0",
            description="daily workstation",
        ),
    )
    payload = paired_profile_payload()
    assert payload["node_id"] == "abc123"
    assert payload["open_ports"] == []
    assert payload["service_records"] == []


def test_profile_route_returns_full_profile_for_paired_controller(monkeypatch) -> None:
    monkeypatch.setattr("homebase_cli.client.is_paired", lambda controller_id: controller_id == "control")
    monkeypatch.setattr(
        "homebase_cli.client.local_profile",
        lambda: parse_profile_payload(
            {
                "node_id": "abc123",
                "node_name": "workstation",
                "hostname": "DESKTOP-SB",
                "platform": "Windows 11",
                "version": "0.1.0",
                "description": "daily workstation",
                "open_ports": [8428],
                "services": ["homebase"],
                "exposed_endpoints": [{"port": 8428, "purpose": "homebase", "owner": "python"}],
                "endpoint_records": [{"port": 8428, "purpose": "homebase", "owner": "python", "pid": 1010}],
                "service_records": [
                    {
                        "name": "homebase",
                        "state": "running",
                        "pid": 1010,
                        "kind": "service",
                        "description": "homebase daemon",
                    }
                ],
            }
        ),
    )
    handler_type = make_handler()
    handler = handler_type.__new__(handler_type)
    handler.path = PROFILE_PATH
    handler.headers = {"X-Homebase-Controller": "control"}
    captured: dict[str, object] = {}
    handler._send_json = lambda payload, status=200: captured.update(payload=payload, status=status)
    handler.send_error = lambda status, message: captured.update(error=(status, message))
    handler.do_GET()
    assert captured["status"] == 200
    payload = captured["payload"]
    assert payload["open_ports"] == (8428,)
    assert payload["service_records"][0]["name"] == "homebase"


def test_pair_route_returns_lightweight_profile_after_success(monkeypatch) -> None:
    monkeypatch.setattr("homebase_cli.client.pairing_rejection_reason", lambda request: None)
    monkeypatch.setattr("homebase_cli.client.pair_controller", lambda request: True)
    monkeypatch.setattr(
        "homebase_cli.client.local_discovery",
        lambda: SimpleNamespace(
            node_id="abc123",
            node_name="workstation",
            hostname="DESKTOP-SB",
            platform="Windows 11",
            version="0.1.0",
            description="daily workstation",
        ),
    )
    body = json.dumps(
        {
            "controller_id": "control",
            "code": "12345678",
            "hostname": "control",
            "address": "192.168.219.107",
        }
    ).encode("utf-8")
    handler_type = make_handler()
    handler = handler_type.__new__(handler_type)
    handler.path = PAIR_PATH
    handler.headers = {"Content-Length": str(len(body)), "Content-Type": "application/json"}
    handler.rfile = io.BytesIO(body)
    captured: dict[str, object] = {}
    handler._send_json = lambda payload, status=200: captured.update(payload=payload, status=status)
    handler.send_error = lambda status, message: captured.update(error=(status, message))
    handler.do_POST()
    assert captured["status"] == 200
    payload = captured["payload"]
    assert payload["node_name"] == "workstation"
    assert payload["open_ports"] == []
    assert payload["service_records"] == []


def test_run_powershell_decodes_utf8_bytes(monkeypatch) -> None:
    monkeypatch.setattr("homebase_cli.client._powershell_binary", lambda: "powershell.exe")
    monkeypatch.setattr(
        "homebase_cli.client.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout='[{"IPAddress":"192.168.0.10","InterfaceAlias":"Wi-Fi"}]'.encode("utf-8"),
            stderr=b"",
        ),
    )
    result = __import__("homebase_cli.client", fromlist=["_run_powershell"])._run_powershell("test")
    assert result is not None
    assert result.stdout == '[{"IPAddress":"192.168.0.10","InterfaceAlias":"Wi-Fi"}]'


def test_subprocess_run_hides_windows_helper_windows(monkeypatch) -> None:
    monkeypatch.setattr("homebase_cli.client.platform_module.system", lambda: "Windows")
    monkeypatch.setattr("homebase_cli.client.subprocess.CREATE_NO_WINDOW", 0x08000000, raising=False)
    captured: dict[str, object] = {}

    def fake_run(*args, **kwargs):
        captured["creationflags"] = kwargs.get("creationflags")
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr="")

    monkeypatch.setattr("homebase_cli.client.subprocess.run", fake_run)
    __import__("homebase_cli.client", fromlist=["_subprocess_run"])._subprocess_run(["tasklist"])
    assert captured["creationflags"] == 0x08000000


def test_interface_addresses_handles_empty_stdout(monkeypatch) -> None:
    monkeypatch.setattr("homebase_cli.client.platform_module.system", lambda: "Windows")
    monkeypatch.setattr(
        "homebase_cli.client._run_powershell",
        lambda script: subprocess.CompletedProcess(args=["powershell"], returncode=0, stdout="", stderr=""),
    )
    assert __import__("homebase_cli.client", fromlist=["_interface_addresses"])._interface_addresses() == {}


def test_connect_server_running_uses_tasklist_on_windows(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "connect-runtime.json"
    save_connect_runtime(
        __import__("homebase_cli.client", fromlist=["ConnectRuntime"]).ConnectRuntime(
            pid=45332,
            host="0.0.0.0",
            port=8428,
            started_at="2026-04-25T00:00:00+00:00",
            log_path="C:\\logs\\connect-server.log",
        ),
        path,
    )
    monkeypatch.setattr("homebase_cli.client.platform_module.system", lambda: "Windows")
    monkeypatch.setattr(
        "homebase_cli.client.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout='"python.exe","45332","Console","1","12,345 K"\n',
            stderr="",
        ),
    )
    runtime = __import__("homebase_cli.client", fromlist=["connect_server_running"]).connect_server_running(path)
    assert runtime is not None
    assert runtime.pid == 45332


def test_connect_server_running_clears_missing_windows_pid(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "connect-runtime.json"
    save_connect_runtime(
        __import__("homebase_cli.client", fromlist=["ConnectRuntime"]).ConnectRuntime(
            pid=45332,
            host="0.0.0.0",
            port=8428,
            started_at="2026-04-25T00:00:00+00:00",
            log_path="C:\\logs\\connect-server.log",
        ),
        path,
    )
    monkeypatch.setattr("homebase_cli.client.platform_module.system", lambda: "Windows")
    monkeypatch.setattr(
        "homebase_cli.client.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="INFO: No tasks are running which match the specified criteria.\n",
            stderr="",
        ),
    )
    runtime = __import__("homebase_cli.client", fromlist=["connect_server_running"]).connect_server_running(path)
    assert runtime is None
    assert not path.exists()


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


def test_pairing_rejection_reason_reports_expired_code(tmp_path: Path) -> None:
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
    reason = pairing_rejection_reason(PairRequest(controller_id="control", code="12345678"), path)
    assert reason == "pairing code expired"


def test_pairing_rejection_reason_reports_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "client-state.json"
    save_client_state(ClientState(pair_code="12345678", paired_controllers=()), path)
    reason = pairing_rejection_reason(PairRequest(controller_id="control", code="87654321"), path)
    assert reason == "pairing code mismatch"


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
    assert endpoints == ((8428, "homebase", "python", 4321),)


def test_detect_endpoint_records_uses_docker_port_owner_fallback(monkeypatch) -> None:
    outputs = iter(
        [
            SimpleNamespace(
                returncode=0,
                stdout='LISTEN 0 4096 0.0.0.0:5000 0.0.0.0:* users:(("docker-proxy",pid=2222,fd=4))\n',
                stderr="",
            ),
            SimpleNamespace(
                returncode=0,
                stdout="eth0 UP 192.168.0.20/24\n",
                stderr="",
            ),
            SimpleNamespace(
                returncode=0,
                stdout="bazarr\t0.0.0.0:5000->6767/tcp, [::]:5000->6767/tcp\n",
                stderr="",
            ),
        ]
    )
    monkeypatch.setattr("homebase_cli.client.platform_module.system", lambda: "Linux")
    monkeypatch.setattr("homebase_cli.client.os.geteuid", lambda: 0)
    monkeypatch.setattr(
        "homebase_cli.client.shutil.which",
        lambda name: "/usr/bin/docker" if name == "docker" else None,
    )
    monkeypatch.setattr("homebase_cli.client.subprocess.run", lambda *args, **kwargs: next(outputs))

    endpoints = detect_endpoint_records()

    assert endpoints == ((5000, "bazarr", "bazarr", 2222),)


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


def test_save_package_job_state_keeps_stage_history(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("homebase_cli.client.PACKAGE_JOB_DIR", tmp_path)
    save_package_job_state("job1", {"job_id": "job1", "step": 1, "total": 6, "label": "accepted request", "status": "running"})
    save_package_job_state("job1", {"job_id": "job1", "step": 2, "total": 6, "label": "preparing source", "status": "running"})
    save_package_job_state("job1", {"job_id": "job1", "step": 2, "total": 6, "label": "preparing source", "status": "running"})
    save_package_job_state("job1", {"job_id": "job1", "step": 6, "total": 6, "label": "done", "status": "done"})
    payload = load_package_job_state("job1")
    assert payload is not None
    assert payload["events"] == [
        {"step": 1, "total": 6, "label": "accepted request", "status": "running"},
        {"step": 2, "total": 6, "label": "preparing source", "status": "running"},
        {"step": 6, "total": 6, "label": "done", "status": "done"},
    ]
