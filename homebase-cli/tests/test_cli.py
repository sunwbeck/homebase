import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import click
import pytest
from typer.testing import CliRunner

from homebase_cli import cli as cli_module
from homebase_cli.client import ClientProfile, ClientState, ConnectRuntime, PairedController
from homebase_cli.scanner import ClientDiscovery, DiscoveredNode
from homebase_cli.selftest import SelfTestResult


def load_app(monkeypatch, settings_path: str | None = None):
    monkeypatch.setenv("HOMEBASE_SETTINGS_PATH", settings_path or ".homebase-test-settings.toml")
    return importlib.reload(cli_module).app


def load_module(monkeypatch, settings_path: str | None = None):
    monkeypatch.setenv("HOMEBASE_SETTINGS_PATH", settings_path or ".homebase-test-settings.toml")
    return importlib.reload(cli_module)


def test_connect_scan_updates_discovery_cache(monkeypatch) -> None:
    runner = CliRunner()
    app = load_app(monkeypatch)
    monkeypatch.setattr("homebase_cli.cli.detect_scannable_networks", lambda: ("192.168.0.0/24",))
    monkeypatch.setattr("homebase_cli.cli.scan_for_clients", lambda cidr, port, timeout: ())
    with runner.isolated_filesystem():
        Path("settings.toml").write_text('role = "controller"\nnode_name = "control"\n', encoding="utf-8")
        result = runner.invoke(
            app,
            ["connect", "scan"],
            env={"HOMEBASE_SETTINGS_PATH": "settings.toml", "HOMEBASE_DISCOVERY_PATH": "discovered.json", "HOMEBASE_REGISTRY_PATH": "nodes.toml"},
        )
        assert result.exit_code == 0
        assert "No homebase clients found" in result.stdout
        payload = json.loads(Path("discovered.json").read_text(encoding="utf-8"))
        assert payload == []


def test_connect_scan_shows_discovered_node_name(monkeypatch) -> None:
    runner = CliRunner()
    app = load_app(monkeypatch)
    monkeypatch.setattr("homebase_cli.cli.detect_scannable_networks", lambda: ("192.168.0.0/24",))
    monkeypatch.setattr(
        "homebase_cli.cli.scan_for_clients",
        lambda cidr, port, timeout: (
            DiscoveredNode(
                address="192.168.0.20",
                port=8428,
                discovery=ClientDiscovery(
                    node_id="abc123",
                    node_name="app",
                    hostname="pve-app",
                    platform="Linux 6.1",
                    version="0.1.0",
                    description="application vm",
                ),
            ),
        ),
    )
    with runner.isolated_filesystem():
        Path("settings.toml").write_text('role = "controller"\nnode_name = "control"\n', encoding="utf-8")
        result = runner.invoke(
            app,
            ["connect", "scan"],
            env={"HOMEBASE_SETTINGS_PATH": "settings.toml", "HOMEBASE_DISCOVERY_PATH": "discovered.json", "HOMEBASE_REGISTRY_PATH": "nodes.toml", "COLUMNS": "240"},
        )
        assert result.exit_code == 0
        assert "Node" in result.stdout
        assert "app" in result.stdout
        assert "pve-app" in result.stdout
        assert "application vm" in result.stdout
        assert "Node ID" not in result.stdout


def test_connect_add_uses_cached_discovery_and_pairing(monkeypatch) -> None:
    runner = CliRunner()
    app = load_app(monkeypatch)
    discovered = [
        {
            "address": "192.168.0.20",
            "port": 8428,
            "discovery": {
                "node_id": "abc123",
                "node_name": "app",
                "hostname": "app",
                "platform": "Linux 6.1",
                "version": "0.1.0",
                "description": "application vm",
                "pairing_required": True,
            },
        }
    ]
    monkeypatch.setattr(
        "homebase_cli.cli.pair_with_client",
        lambda address, code, port=8428: ClientProfile(
            node_id="abc123",
            node_name="app",
            hostname="app",
            platform="Linux 6.1",
            version="0.1.0",
            description="application vm",
            open_ports=(22, 8080),
            services=("ssh", "docker"),
        ) if code == "12345678" else None,
    )
    with runner.isolated_filesystem():
        Path("settings.toml").write_text('role = "controller"\nnode_name = "control"\n', encoding="utf-8")
        Path("discovered.json").write_text(json.dumps(discovered), encoding="utf-8")
        result = runner.invoke(
            app,
            ["connect", "add", "host.app"],
            env={"HOMEBASE_SETTINGS_PATH": "settings.toml", "HOMEBASE_DISCOVERY_PATH": "discovered.json", "HOMEBASE_REGISTRY_PATH": "nodes.toml"},
            input="1\n12345678\n1\nsun\napp vm\n",
        )
        assert result.exit_code == 0
        assert "Registered node host.app" in result.stdout
        assert "Hierarchy" not in result.stdout
        assert "SSH user" not in result.stdout


def test_connect_add_fails_when_pairing_code_is_wrong(monkeypatch) -> None:
    runner = CliRunner()
    app = load_app(monkeypatch)
    discovered = [
        {
            "address": "192.168.0.20",
            "port": 8428,
            "discovery": {
                "node_id": "abc123",
                "node_name": "app",
                "hostname": "app",
                "platform": "Linux 6.1",
                "version": "0.1.0",
                "description": "application vm",
                "pairing_required": True,
            },
        }
    ]
    monkeypatch.setattr("homebase_cli.cli.pair_with_client", lambda address, code, port=8428: None)
    with runner.isolated_filesystem():
        Path("settings.toml").write_text('role = "controller"\nnode_name = "control"\n', encoding="utf-8")
        Path("discovered.json").write_text(json.dumps(discovered), encoding="utf-8")
        result = runner.invoke(
            app,
            ["connect", "add", "host.app"],
            env={"HOMEBASE_SETTINGS_PATH": "settings.toml", "HOMEBASE_DISCOVERY_PATH": "discovered.json", "HOMEBASE_REGISTRY_PATH": "nodes.toml"},
            input="1\n12345678\n",
        )
        assert result.exit_code != 0
        assert "pairing failed" in result.output


def test_init_sets_controller_role(monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        app = load_app(monkeypatch, "settings.toml")
        result = runner.invoke(
            app,
            ["init", "--role", "control", "--name", "control", "--description", "main controller"],
            env={"HOMEBASE_SETTINGS_PATH": "settings.toml"},
        )
        assert result.exit_code == 0
        assert "Set local node type to controller" in result.stdout
        assert "Registered local node name: control" in result.stdout
        assert "Registered local description: main controller" in result.stdout


def test_role_edit_without_args_prompts_for_local_role(monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        env = {"HOMEBASE_SETTINGS_PATH": "settings.toml"}
        Path("settings.toml").write_text('role = "managed"\nnode_name = "app"\n', encoding="utf-8")
        app = load_app(monkeypatch, "settings.toml")
        picks = iter(["app (local)", "controller"])
        monkeypatch.setattr("homebase_cli.cli._pick_from_list", lambda label, options: next(picks))
        result = runner.invoke(app, ["role", "edit"], env=env)
        assert result.exit_code == 0
        assert "Set local node type to controller" in result.stdout


def test_role_edit_without_args_prompts_for_node_and_role(monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        env = {"HOMEBASE_SETTINGS_PATH": "settings.toml", "HOMEBASE_REGISTRY_PATH": "nodes.toml"}
        Path("settings.toml").write_text('role = "controller"\nnode_name = "control"\n', encoding="utf-8")
        Path("nodes.toml").write_text('[[nodes]]\nname = "host.app"\nkind = "vm"\nruntime_role = "managed"\n', encoding="utf-8")
        app = load_app(monkeypatch, "settings.toml")
        picks = iter(["host.app", "controller"])
        monkeypatch.setattr("homebase_cli.cli._pick_from_list", lambda label, options: next(picks))
        result = runner.invoke(app, ["role", "edit"], env=env)
        assert result.exit_code == 0
        assert "Set node role:" in result.stdout
        assert "host.app -> controller" in result.stdout


def test_init_rejects_unknown_role(monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        app = load_app(monkeypatch, "settings.toml")
        result = runner.invoke(app, ["init", "--role", "builder", "--name", "control"], env={"HOMEBASE_SETTINGS_PATH": "settings.toml"})
        assert result.exit_code != 0
        assert "role must be one of: controller, managed" in result.output


def test_init_interactive_can_choose_managed(monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        app = load_app(monkeypatch, "settings.toml")
        result = runner.invoke(app, ["init"], env={"HOMEBASE_SETTINGS_PATH": "settings.toml"}, input="2\napp\napplication vm\n")
        assert result.exit_code == 0
        assert "Initial setup" in result.stdout
        assert "Choose how this node will participate in homebase." in result.stdout
        assert "local runtime role" in result.stdout
        assert "Local node name" in result.stdout
        assert "Local description" in result.stdout
        assert "Set local node type to managed" in result.stdout
        assert "Registered local node name: app" in result.stdout
        assert "Registered local description: application vm" in result.stdout


def test_pick_from_list_accepts_exact_value(monkeypatch) -> None:
    module = load_module(monkeypatch, "settings.toml")
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "managed")
    assert module._pick_from_list("Local runtime role", ("controller", "managed")) == "managed"


def test_main_starts_init_automatically_when_uninitialized(monkeypatch, capsys) -> None:
    module = load_module(monkeypatch, "settings.toml")
    called: dict[str, bool] = {"ran": False}
    monkeypatch.setattr(module, "_run_init", lambda role=None, name=None, description=None: called.__setitem__("ran", True))
    monkeypatch.setattr(sys, "argv", ["hb"])
    module.main()
    captured = capsys.readouterr()
    assert called["ran"] is True
    assert "Starting init" in captured.out


def test_main_handles_abort_without_traceback(monkeypatch, capsys) -> None:
    module = load_module(monkeypatch, "settings.toml")
    monkeypatch.setattr(module, "_run_init", lambda role=None, name=None, description=None: (_ for _ in ()).throw(click.Abort()))
    monkeypatch.setattr(sys, "argv", ["hb"])
    with pytest.raises(SystemExit) as exc:
        module.main()
    captured = capsys.readouterr()
    assert exc.value.code == 130
    assert "Cancelled." in captured.out


def test_root_help_is_concise(monkeypatch) -> None:
    runner = CliRunner()
    app = load_app(monkeypatch)
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "connect it to homebase" in result.stdout
    assert "Start with:" not in result.stdout


def test_root_help_for_managed_hides_controller_only_commands(monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        Path("settings.toml").write_text('role = "managed"\nnode_name = "app"\n', encoding="utf-8")
        app = load_app(monkeypatch, "settings.toml")
        result = runner.invoke(app, ["--help"], env={"HOMEBASE_SETTINGS_PATH": "settings.toml"})
        assert result.exit_code == 0
        assert "│ status" in result.stdout
        assert "│ node " not in result.stdout
        assert "│ group" not in result.stdout
        assert "│ link " not in result.stdout
        assert "│ inventory" not in result.stdout
        assert "│ connect" in result.stdout
        assert "│ service" in result.stdout
        assert "│ package" in result.stdout
        assert "│ role" in result.stdout


def test_root_help_for_controller_hides_removed_state_command(monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        Path("settings.toml").write_text('role = "controller"\nnode_name = "control"\n', encoding="utf-8")
        app = load_app(monkeypatch, "settings.toml")
        result = runner.invoke(app, ["--help"], env={"HOMEBASE_SETTINGS_PATH": "settings.toml"})
        assert result.exit_code == 0
        assert "│ state" not in result.stdout


def test_init_help_explains_role_and_name(monkeypatch) -> None:
    runner = CliRunner()
    app = load_app(monkeypatch)
    result = runner.invoke(app, ["init", "--help"])
    assert result.exit_code == 0
    assert "choosing the local role and node name" in result.stdout.lower()


def test_inventory_help_points_to_show_and_edit(monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        env = {"HOMEBASE_SETTINGS_PATH": "settings.toml"}
        Path("settings.toml").write_text('role = "controller"\nnode_name = "control"\n', encoding="utf-8")
        app = load_app(monkeypatch, "settings.toml")
        result = runner.invoke(app, ["inventory", "--help"], env=env)
        assert result.exit_code == 0
        assert "Work with the rendered ansible inventory file." in result.stdout


def test_doc_lists_repo_docs(monkeypatch) -> None:
    runner = CliRunner()
    app = load_app(monkeypatch)
    result = runner.invoke(app, ["doc"])
    assert result.exit_code == 0
    assert "Homebase Docs" in result.stdout
    assert "Use `homebase doc <key>`" in result.stdout


def test_doc_prints_document_content(monkeypatch) -> None:
    runner = CliRunner()
    app = load_app(monkeypatch)
    result = runner.invoke(app, ["doc", "current-state"])
    assert result.exit_code == 0
    assert "Current State" in result.stdout
    assert "current implementation direction" in result.stdout.lower()


def test_status_shows_local_node(monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        env = {"HOMEBASE_SETTINGS_PATH": "settings.toml", "HOMEBASE_REGISTRY_PATH": "nodes.toml", "COLUMNS": "240"}
        app = load_app(monkeypatch, "settings.toml")
        result = runner.invoke(app, ["init", "--role", "controller", "--name", "control", "--description", "main controller"], env=env)
        assert result.exit_code == 0
        app = load_app(monkeypatch, "settings.toml")
        status_result = runner.invoke(app, ["status"], env=env)
        assert status_result.exit_code == 0
        assert "Node status" in status_result.stdout
        assert "control" in status_result.stdout
        assert "controller" in status_result.stdout
        assert "Exposure" in status_result.stdout
        assert "State" not in status_result.stdout


def test_managed_status_shows_self_and_paired_controllers(monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        env = {"HOMEBASE_SETTINGS_PATH": "settings.toml", "COLUMNS": "240"}
        Path("settings.toml").write_text('role = "managed"\nnode_name = "app"\n', encoding="utf-8")
        app = load_app(monkeypatch, "settings.toml")
        monkeypatch.setattr(
            "homebase_cli.cli.local_profile",
            lambda: ClientProfile(
                node_id="node-1",
                node_name="app",
                hostname="app",
                platform="Linux 6.1",
                version="0.1.8",
                open_ports=(22, 8080),
                services=("ssh", "docker"),
            ),
        )
        monkeypatch.setattr("homebase_cli.cli.detect_primary_address", lambda: "192.168.0.20")
        monkeypatch.setattr("homebase_cli.cli.connect_server_running", lambda: None)
        monkeypatch.setattr(
            "homebase_cli.cli.detect_exposed_endpoints",
            lambda: ((22, "ssh", "sshd"), (8428, "homebase", "python")),
        )
        monkeypatch.setattr(
            "homebase_cli.cli.load_client_state",
            lambda: ClientState(
                pair_code="12345678",
                paired_controllers=(
                    PairedController(
                        controller_id="control-id",
                        hostname="control",
                        address="192.168.0.10",
                    ),
                ),
            ),
        )
        result = runner.invoke(app, ["status"], env=env)
        assert result.exit_code == 0
        assert "app (local)" in result.stdout
        assert "192.168.0.20" in result.stdout
        assert "control" in result.stdout
        assert "192.168.0.10" in result.stdout
        assert "ssh:22" in result.stdout
        assert "homebase:8428" in result.stdout
        assert "State" not in result.stdout


def test_daemon_status_includes_local_identity(monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        env = {"HOMEBASE_SETTINGS_PATH": "settings.toml", "COLUMNS": "240"}
        Path("settings.toml").write_text('role = "managed"\nnode_name = "app"\n', encoding="utf-8")
        app = load_app(monkeypatch, "settings.toml")
        monkeypatch.setattr("homebase_cli.cli.detect_primary_address", lambda: "192.168.0.20")
        monkeypatch.setattr("homebase_cli.cli.socket.gethostname", lambda: "app")
        monkeypatch.setattr(
            "homebase_cli.cli.connect_server_running",
            lambda: ConnectRuntime(
                pid=4321,
                host="0.0.0.0",
                port=8428,
                started_at="2026-04-23T00:00:00Z",
                log_path="/tmp/connect.log",
            ),
        )
        result = runner.invoke(app, ["daemon", "status"], env=env)
        assert result.exit_code == 0
        assert "app" in result.stdout
        assert "managed" in result.stdout
        assert "192.168.0.20" in result.stdout
        assert "0.0.0.0:8428" in result.stdout


def test_connect_code_always_refreshes_and_shows_expiry(monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        env = {"HOMEBASE_SETTINGS_PATH": "settings.toml"}
        Path("settings.toml").write_text('role = "managed"\nnode_name = "app"\n', encoding="utf-8")
        app = load_app(monkeypatch, "settings.toml")
        monkeypatch.setattr(
            "homebase_cli.cli.refresh_pair_code",
            lambda: ClientState(pair_code="12345678", pair_code_expires_at="2026-04-24T22:30:00+09:00", paired_controllers=()),
        )
        result = runner.invoke(app, ["connect", "code"], env=env)
        assert result.exit_code == 0
        assert "1234 5678" in result.stdout
        assert "spaces are optional" in result.stdout
        assert "expires:" in result.stdout


def test_service_list_shows_local_services(monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        env = {"HOMEBASE_SETTINGS_PATH": "settings.toml", "COLUMNS": "240"}
        Path("settings.toml").write_text('role = "managed"\nnode_name = "app"\n', encoding="utf-8")
        app = load_app(monkeypatch, "settings.toml")
        monkeypatch.setattr(
            "homebase_cli.cli.find_node",
            lambda name: SimpleNamespace(
                name="app",
                address="192.168.0.20",
                runtime_hostname="app",
                platform="Linux 6.1",
                open_ports=(22, 8428),
                services=("ssh", "docker"),
                exposed_endpoints=((22, "ssh", "sshd"), (8428, "homebase", "python")),
                service_records=(("ssh", "running", 111, "systemd", "OpenSSH server"), ("docker", "running", None, "docker", "Docker Engine")),
            ),
        )
        monkeypatch.setattr(
            "homebase_cli.cli.local_profile",
            lambda: ClientProfile(
                node_id="node-1",
                node_name="app",
                hostname="app",
                platform="Linux 6.1",
                version="0.1.8",
                services=("ssh", "docker"),
                endpoint_records=((22, "ssh", "sshd", 111), (8428, "homebase", "python", 222)),
                service_records=(("ssh", "running", 111, "systemd", "OpenSSH server"), ("docker", "running", None, "docker", "Docker Engine")),
            ),
        )
        monkeypatch.setattr("homebase_cli.cli.detect_exposed_endpoints", lambda: ((22, "ssh", "sshd"), (8428, "homebase", "python")))
        result = runner.invoke(app, ["service", "list"], env=env)
        assert result.exit_code == 0
        assert "Service" in result.stdout
        assert "State" in result.stdout
        assert "PID" in result.stdout
        assert "Ports" in result.stdout
        assert "192.168.0.20" in result.stdout
        assert "ssh" in result.stdout
        assert "docker" in result.stdout
        assert "running" in result.stdout
        assert "111" in result.stdout
        assert "22" in result.stdout
        assert "OpenSSH server" in result.stdout
        assert "homebase" in result.stdout
        assert "listening" in result.stdout


def test_service_list_shows_non_running_records(monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        env = {"HOMEBASE_SETTINGS_PATH": "settings.toml", "COLUMNS": "240"}
        Path("settings.toml").write_text('role = "managed"\nnode_name = "app"\n', encoding="utf-8")
        app = load_app(monkeypatch, "settings.toml")
        monkeypatch.setattr(
            "homebase_cli.cli.find_node",
            lambda name: SimpleNamespace(
                name="app",
                address="192.168.0.20",
                runtime_hostname="app",
                platform="Linux 6.1",
                open_ports=(22,),
                services=("ssh",),
                exposed_endpoints=((22, "ssh", "sshd"),),
                service_records=(
                    ("ssh", "running", 111, "systemd", "OpenSSH server"),
                    ("apt-daily", "dead", None, "systemd", "Daily apt download"),
                ),
            ),
        )
        monkeypatch.setattr(
            "homebase_cli.cli.local_profile",
            lambda: ClientProfile(
                node_id="node-1",
                node_name="app",
                hostname="app",
                platform="Linux 6.1",
                version="0.1.8",
                services=("ssh",),
                service_records=(
                    ("ssh", "running", 111, "systemd", "OpenSSH server"),
                    ("apt-daily", "dead", None, "systemd", "Daily apt download"),
                ),
            ),
        )
        monkeypatch.setattr("homebase_cli.cli.detect_exposed_endpoints", lambda: ((22, "ssh", "sshd"),))
        result = runner.invoke(app, ["service", "list"], env=env)
        assert result.exit_code == 0
        assert "ssh" in result.stdout
        assert "apt-daily" in result.stdout
        assert "dead" in result.stdout


def test_service_list_matches_endpoints_by_pid(monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        env = {"HOMEBASE_SETTINGS_PATH": "settings.toml", "COLUMNS": "240"}
        Path("settings.toml").write_text('role = "managed"\nnode_name = "app"\n', encoding="utf-8")
        app = load_app(monkeypatch, "settings.toml")
        monkeypatch.setattr(
            "homebase_cli.cli.find_node",
            lambda name: SimpleNamespace(
                name="app",
                address="192.168.0.20",
                runtime_hostname="app",
                platform="Linux 6.1",
                open_ports=(80, 443),
                services=("caddy",),
                exposed_endpoints=((80, "http", "caddy"), (443, "https", "caddy")),
                endpoint_records=((80, "http", "caddy", 37120), (443, "https", "caddy", 37120)),
                service_records=(("caddy", "running", 37120, "systemd", "Caddy"),),
            ),
        )
        monkeypatch.setattr(
            "homebase_cli.cli.local_profile",
            lambda: ClientProfile(
                node_id="node-1",
                node_name="app",
                hostname="app",
                platform="Linux 6.1",
                version="0.1.8",
                services=("caddy",),
                endpoint_records=((80, "http", "caddy", 37120), (443, "https", "caddy", 37120)),
                service_records=(("caddy", "running", 37120, "systemd", "Caddy"),),
            ),
        )
        result = runner.invoke(app, ["service", "list"], env=env)
        assert result.exit_code == 0
        assert "caddy" in result.stdout
        assert "80, 443" in result.stdout
        assert "http" not in result.stdout
        assert "https" not in result.stdout
        assert "listening" not in result.stdout


def test_service_list_marks_nodes_without_endpoints(monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        env = {"HOMEBASE_SETTINGS_PATH": "settings.toml", "HOMEBASE_REGISTRY_PATH": "nodes.toml", "COLUMNS": "240"}
        Path("settings.toml").write_text('role = "controller"\nnode_name = "control"\n', encoding="utf-8")
        Path("nodes.toml").write_text(
            '[[nodes]]\nname = "app"\nkind = "vm"\nruntime_role = "managed"\nruntime_hostname = "app"\n',
            encoding="utf-8",
        )
        app = load_app(monkeypatch, "settings.toml")
        result = runner.invoke(app, ["service", "list"], env=env)
        assert result.exit_code == 0
        assert "none" in result.stdout


def test_service_show_uses_runtime_address(monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        env = {"HOMEBASE_SETTINGS_PATH": "settings.toml", "HOMEBASE_REGISTRY_PATH": "nodes.toml", "COLUMNS": "240"}
        Path("settings.toml").write_text('role = "controller"\nnode_name = "control"\n', encoding="utf-8")
        Path("nodes.toml").write_text(
            '\n'.join(
                [
                    '[[nodes]]',
                    'name = "control"',
                    'kind = "controller"',
                    'runtime_role = "controller"',
                    'runtime_hostname = "control"',
                    'description = "main controller"',
                    '',
                ]
            ),
            encoding="utf-8",
        )
        app = load_app(monkeypatch, "settings.toml")
        monkeypatch.setattr("homebase_cli.cli.detect_primary_address", lambda: "192.168.0.10")
        monkeypatch.setattr(
            "homebase_cli.cli.local_profile",
            lambda: ClientProfile(
                node_id="node-1",
                node_name="control",
                hostname="control",
                platform="Linux 6.1",
                version="0.1.15",
                services=("ssh", "homebase"),
                service_records=(("ssh", "running", 999, "systemd", "OpenSSH server"),),
            ),
        )
        monkeypatch.setattr("homebase_cli.cli.detect_running_services", lambda: ("ssh", "homebase", "caddy"))
        monkeypatch.setattr("homebase_cli.cli.detect_exposed_endpoints", lambda: ((22, "ssh", "sshd"), (8428, "homebase", "python")))
        result = runner.invoke(app, ["service", "show", "control"], env=env)
        assert result.exit_code == 0
        assert "address" in result.stdout
        assert "192.168.0.10" in result.stdout
        assert "22 -> ssh (sshd)" in result.stdout
        assert "Service records" in result.stdout
        assert "running" in result.stdout
        assert "999" in result.stdout


def test_service_search_matches_multiple_terms(monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        env = {"HOMEBASE_SETTINGS_PATH": "settings.toml", "HOMEBASE_REGISTRY_PATH": "nodes.toml", "COLUMNS": "240"}
        Path("settings.toml").write_text('role = "controller"\nnode_name = "control"\n', encoding="utf-8")
        Path("nodes.toml").write_text(
            '\n'.join(
                [
                    '[[nodes]]',
                    'name = "app"',
                    'kind = "vm"',
                    'runtime_role = "managed"',
                    'address = "192.168.0.20"',
                    'runtime_hostname = "app"',
                    'services = ["grafana"]',
                    'exposed_endpoints = ["3000|grafana|docker"]',
                    'service_records = ["grafana|running||docker|Grafana container"]',
                    '',
                ]
            ),
            encoding="utf-8",
        )
        app = load_app(monkeypatch, "settings.toml")
        result = runner.invoke(app, ["service", "search", "graf"], env=env)
        assert result.exit_code == 0
        assert "grafana" in result.stdout
        result = runner.invoke(app, ["service", "search", "app", "running", "3000"], env=env)
        assert result.exit_code == 0
        assert "3000" in result.stdout
        assert "running" in result.stdout


def test_service_start_requests_remote_action(monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        env = {"HOMEBASE_SETTINGS_PATH": "settings.toml", "HOMEBASE_REGISTRY_PATH": "nodes.toml"}
        Path("settings.toml").write_text('role = "controller"\nnode_name = "control"\n', encoding="utf-8")
        Path("nodes.toml").write_text(
            '[[nodes]]\nname = "app"\nkind = "vm"\nruntime_role = "managed"\naddress = "192.168.0.20"\nclient_port = 8428\n',
            encoding="utf-8",
        )
        app = load_app(monkeypatch, "settings.toml")
        called = {}
        monkeypatch.setattr(
            "homebase_cli.cli.request_service_action",
            lambda address, service, action, port=8428: called.update({"address": address, "service": service, "action": action, "port": port}) or {},
        )
        result = runner.invoke(app, ["service", "start", "app", "grafana"], env=env)
        assert result.exit_code == 0
        assert called == {"address": "192.168.0.20", "service": "grafana", "action": "start", "port": 8428}


def test_service_stop_uses_local_control(monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        env = {"HOMEBASE_SETTINGS_PATH": "settings.toml", "HOMEBASE_REGISTRY_PATH": "nodes.toml"}
        Path("settings.toml").write_text('role = "managed"\nnode_name = "app"\n', encoding="utf-8")
        Path("nodes.toml").write_text('[[nodes]]\nname = "app"\nkind = "node"\nruntime_role = "managed"\n', encoding="utf-8")
        app = load_app(monkeypatch, "settings.toml")
        called = {}
        monkeypatch.setattr(
            "homebase_cli.cli.control_service",
            lambda service, action: called.update({"service": service, "action": action}),
        )
        result = runner.invoke(app, ["service", "stop", "grafana"], env=env)
        assert result.exit_code == 0
        assert called == {"service": "grafana", "action": "stop"}


def test_node_edit_name_updates_local_node_name(monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        env = {"HOMEBASE_SETTINGS_PATH": "settings.toml", "HOMEBASE_REGISTRY_PATH": "nodes.toml"}
        Path("settings.toml").write_text('role = "controller"\nnode_name = "host.app"\n', encoding="utf-8")
        Path("nodes.toml").write_text('[[nodes]]\nname = "host.app"\nkind = "vm"\nruntime_role = "managed"\n', encoding="utf-8")
        app = load_app(monkeypatch, "settings.toml")
        picks = iter(["host.app (local)", "name"])
        monkeypatch.setattr("homebase_cli.cli._pick_from_list", lambda label, options: next(picks))
        result = runner.invoke(app, ["node", "edit"], env=env, input="host.api\n")
        assert result.exit_code == 0
        assert 'node_name = "host.api"' in Path("settings.toml").read_text(encoding="utf-8")


def test_node_edit_blank_input_keeps_current_name(monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        env = {"HOMEBASE_SETTINGS_PATH": "settings.toml", "HOMEBASE_REGISTRY_PATH": "nodes.toml"}
        Path("settings.toml").write_text('role = "controller"\nnode_name = "app"\n', encoding="utf-8")
        Path("nodes.toml").write_text('[[nodes]]\nname = "app"\nkind = "vm"\nruntime_role = "managed"\n', encoding="utf-8")
        app = load_app(monkeypatch, "settings.toml")
        picks = iter(["app (local)", "name"])
        monkeypatch.setattr("homebase_cli.cli._pick_from_list", lambda label, options: next(picks))
        result = runner.invoke(app, ["node", "edit"], env=env, input="\n")
        assert result.exit_code == 0
        assert "Renamed node:" in result.stdout
        assert "app -> app" in result.stdout


def test_node_edit_description_updates_description(monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        env = {"HOMEBASE_SETTINGS_PATH": "settings.toml", "HOMEBASE_REGISTRY_PATH": "nodes.toml"}
        Path("settings.toml").write_text('role = "controller"\nnode_name = "control"\n', encoding="utf-8")
        Path("nodes.toml").write_text('[[nodes]]\nname = "app"\nkind = "vm"\nruntime_role = "managed"\n', encoding="utf-8")
        app = load_app(monkeypatch, "settings.toml")
        picks = iter(["app", "description"])
        monkeypatch.setattr("homebase_cli.cli._pick_from_list", lambda label, options: next(picks))
        result = runner.invoke(app, ["node", "edit"], env=env, input="application vm\n")
        assert result.exit_code == 0
        assert "Updated node description:" in result.stdout


def test_node_edit_description_updates_local_settings_when_local(monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        env = {"HOMEBASE_SETTINGS_PATH": "settings.toml", "HOMEBASE_REGISTRY_PATH": "nodes.toml"}
        Path("settings.toml").write_text(
            'role = "controller"\nnode_name = "control"\nnode_description = "old description"\n',
            encoding="utf-8",
        )
        Path("nodes.toml").write_text(
            '[[nodes]]\nname = "control"\nkind = "controller"\nruntime_role = "controller"\ndescription = "old description"\n',
            encoding="utf-8",
        )
        app = load_app(monkeypatch, "settings.toml")
        picks = iter(["control (local)", "description"])
        monkeypatch.setattr("homebase_cli.cli._pick_from_list", lambda label, options: next(picks))
        result = runner.invoke(app, ["node", "edit"], env=env, input="new description\n")
        assert result.exit_code == 0
        assert 'node_description = "new description"' in Path("settings.toml").read_text(encoding="utf-8")


def test_node_list_shows_address_and_description(monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        env = {"HOMEBASE_SETTINGS_PATH": "settings.toml", "HOMEBASE_REGISTRY_PATH": "nodes.toml", "COLUMNS": "240"}
        Path("settings.toml").write_text('role = "controller"\nnode_name = "control"\n', encoding="utf-8")
        Path("nodes.toml").write_text(
            '[[nodes]]\nname = "app"\nkind = "vm"\nruntime_role = "managed"\naddress = "192.168.0.20"\ndescription = "application vm"\n',
            encoding="utf-8",
        )
        app = load_app(monkeypatch, "settings.toml")
        result = runner.invoke(app, ["node", "list"], env=env)
        assert result.exit_code == 0
        assert "Address" in result.stdout
        assert "Description" in result.stdout
        assert "192.168.0.20" in result.stdout
        assert "application vm" in result.stdout


def test_node_list_uses_live_local_address(monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        env = {"HOMEBASE_SETTINGS_PATH": "settings.toml", "HOMEBASE_REGISTRY_PATH": "nodes.toml", "COLUMNS": "240"}
        Path("settings.toml").write_text('role = "controller"\nnode_name = "control"\n', encoding="utf-8")
        Path("nodes.toml").write_text(
            '[[nodes]]\nname = "control"\nkind = "controller"\nruntime_role = "controller"\ndescription = "main controller"\n',
            encoding="utf-8",
        )
        app = load_app(monkeypatch, "settings.toml")
        monkeypatch.setattr("homebase_cli.cli.detect_primary_address", lambda: "192.168.0.10")
        monkeypatch.setattr("homebase_cli.cli.socket.gethostname", lambda: "control")
        result = runner.invoke(app, ["node", "list"], env=env)
        assert result.exit_code == 0
        assert "192.168.0.10" in result.stdout


def test_node_show_uses_endpoint_details_without_states(monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        env = {"HOMEBASE_SETTINGS_PATH": "settings.toml", "HOMEBASE_REGISTRY_PATH": "nodes.toml", "COLUMNS": "240"}
        Path("settings.toml").write_text('role = "controller"\nnode_name = "control"\n', encoding="utf-8")
        Path("nodes.toml").write_text(
            '\n'.join(
                [
                    '[[nodes]]',
                    'name = "app"',
                    'kind = "vm"',
                    'runtime_role = "managed"',
                    'runtime_hostname = "app"',
                    'address = "192.168.0.20"',
                    'exposed_endpoints = ["22|ssh|sshd", "8428|homebase|python"]',
                    '',
                ]
            ),
            encoding="utf-8",
        )
        app = load_app(monkeypatch, "settings.toml")
        result = runner.invoke(app, ["node", "show", "app"], env=env)
        assert result.exit_code == 0
        assert "22 -> ssh (sshd)" in result.stdout
        assert "8428 -> homebase (python)" in result.stdout
        assert "states" not in result.stdout.lower()


def test_group_edit_without_args_prompts_for_group_and_field(monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        env = {"HOMEBASE_SETTINGS_PATH": "settings.toml", "HOMEBASE_REGISTRY_PATH": "nodes.toml"}
        Path("settings.toml").write_text('role = "controller"\nnode_name = "control"\n', encoding="utf-8")
        app = load_app(monkeypatch, "settings.toml")
        assert runner.invoke(app, ["group", "add", "app-tier"], env=env).exit_code == 0
        picks = iter(["app-tier", "name"])
        monkeypatch.setattr("homebase_cli.cli._pick_from_list", lambda label, options: next(picks))
        result = runner.invoke(app, ["group", "edit"], env=env, input="app-services\n")
        assert result.exit_code == 0
        assert "Renamed group:" in result.stdout
        assert "app-services" in result.stdout


def test_node_remove_removes_registered_node(monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        env = {"HOMEBASE_SETTINGS_PATH": "settings.toml", "HOMEBASE_REGISTRY_PATH": "nodes.toml"}
        Path("settings.toml").write_text('role = "controller"\nnode_name = "control"\n', encoding="utf-8")
        Path("nodes.toml").write_text('[[nodes]]\nname = "host.app"\nkind = "vm"\nruntime_role = "managed"\n', encoding="utf-8")
        app = load_app(monkeypatch, "settings.toml")
        monkeypatch.setattr("homebase_cli.cli._pick_from_list", lambda label, options: "host.app")
        result = runner.invoke(app, ["node", "remove"], env=env)
        assert result.exit_code == 0
        assert "Removed node:" in result.stdout
        assert "host.app" in result.stdout


def test_connect_remove_matches_node_remove(monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        env = {"HOMEBASE_SETTINGS_PATH": "settings.toml", "HOMEBASE_REGISTRY_PATH": "nodes.toml"}
        Path("settings.toml").write_text('role = "controller"\nnode_name = "control"\n', encoding="utf-8")
        Path("nodes.toml").write_text('[[nodes]]\nname = "host.app"\nkind = "vm"\nruntime_role = "managed"\n', encoding="utf-8")
        app = load_app(monkeypatch, "settings.toml")
        monkeypatch.setattr("homebase_cli.cli._pick_from_list", lambda label, options: "host.app")
        result = runner.invoke(app, ["connect", "remove"], env=env)
        assert result.exit_code == 0
        assert "Removed node:" in result.stdout


def test_group_and_link_commands_build_hierarchy(monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        env = {"HOMEBASE_SETTINGS_PATH": "settings.toml", "HOMEBASE_REGISTRY_PATH": "nodes.toml"}
        Path("settings.toml").write_text('role = "controller"\nnode_name = "control"\n', encoding="utf-8")
        Path("nodes.toml").write_text(
            '[[nodes]]\nname = "host.app"\nkind = "vm"\nruntime_role = "managed"\n',
            encoding="utf-8",
        )
        app = load_app(monkeypatch, "settings.toml")
        assert runner.invoke(app, ["group", "add", "host-node"], env=env).exit_code == 0
        assert runner.invoke(app, ["group", "add", "app-tier"], env=env).exit_code == 0
        assert runner.invoke(app, ["link", "add", "host-node", "app-tier"], env=env).exit_code == 0
        assert runner.invoke(app, ["node", "assign", "host.app", "app-tier"], env=env).exit_code == 0
        group_result = runner.invoke(app, ["group", "show", "app-tier"], env=env)
        assert group_result.exit_code == 0
        assert "assigned nodes" in group_result.stdout
        assert "host.app" in group_result.stdout
        link_result = runner.invoke(app, ["link", "show", "host-node"], env=env)
        assert link_result.exit_code == 0
        assert "app-tier" in link_result.stdout


def test_package_versions_prints_github_versions(monkeypatch) -> None:
    runner = CliRunner()
    app = load_app(monkeypatch)
    monkeypatch.setattr(
        "homebase_cli.cli.github_versions",
        lambda repo_url, include_prerelease=False: (
            SimpleNamespace(version="v0.1.1", source="release", published_at="2026-04-23T00:00:00Z", summary="pairing fix"),
        ),
    )
    result = runner.invoke(app, ["package", "versions"])
    assert result.exit_code == 0
    assert "pairing fix" in result.stdout
    assert "Ref" in result.stdout
    assert "Kind" in result.stdout


def test_package_status_prints_local_install_state(monkeypatch) -> None:
    runner = CliRunner()
    app = load_app(monkeypatch)
    monkeypatch.setattr(
        "homebase_cli.cli.load_install_state",
        lambda: SimpleNamespace(
            installed_version="0.1.1",
            repo_url="https://github.com/sunwbeck/homebase.git",
            requested_ref="v0.1.1",
            resolved_ref="abc123",
            summary="pairing fix",
            installed_at="2026-04-23T00:00:00Z",
        ),
    )
    monkeypatch.setattr(
        "homebase_cli.cli.latest_github_version",
        lambda repo_url, include_prerelease=False: SimpleNamespace(version="v0.1.2", summary="latest note"),
    )
    result = runner.invoke(app, ["package", "status"])
    assert result.exit_code == 0
    assert "installed version: 0.1.1" in result.stdout
    assert "latest available: v0.1.2" in result.stdout


def test_package_install_can_target_explicit_python(monkeypatch) -> None:
    runner = CliRunner()
    app = load_app(monkeypatch)
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        "homebase_cli.cli.install_github_ref",
        lambda ref, repo_url, python_bin=None, summary=None, on_tick=None: (
            seen.update({"python_bin": python_bin, "ref": ref}) or SimpleNamespace(returncode=0, stdout="installed\n", stderr="", log_path=Path("/tmp/install.log")),
            SimpleNamespace(installed_version="0.1.1", requested_ref=ref, resolved_ref="abc123"),
        ),
    )
    result = runner.invoke(app, ["package", "install", "--ref", "v0.1.1", "--python", "/tmp/custom/bin/python"])
    assert result.exit_code == 0
    assert seen == {"python_bin": "/tmp/custom/bin/python", "ref": "v0.1.1"}
    assert "Installed into Python: /tmp/custom/bin/python" in result.stdout


def test_package_update_uses_latest_github_version(monkeypatch) -> None:
    runner = CliRunner()
    app = load_app(monkeypatch)
    monkeypatch.setattr(
        "homebase_cli.cli.latest_github_version",
        lambda repo_url, include_prerelease=False: SimpleNamespace(ref="v0.1.2", version="v0.1.2", summary="latest note"),
    )
    monkeypatch.setattr(
        "homebase_cli.cli.install_github_ref",
        lambda ref, repo_url, python_bin=None, summary=None, on_tick=None: (
            SimpleNamespace(returncode=0, stdout="installed\n", stderr="", log_path=Path("/tmp/install.log")),
            SimpleNamespace(installed_version="0.1.2", requested_ref=ref, resolved_ref="def456"),
        ),
    )
    result = runner.invoke(app, ["package", "update"])
    assert result.exit_code == 0
    assert "Selected latest target: v0.1.2" in result.stdout


def test_connect_help_for_controller_hides_code(monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        Path("settings.toml").write_text('role = "controller"\nnode_name = "control"\n', encoding="utf-8")
        app = load_app(monkeypatch, "settings.toml")
        result = runner.invoke(app, ["connect", "--help"], env={"HOMEBASE_SETTINGS_PATH": "settings.toml"})
        assert result.exit_code == 0
        code_result = runner.invoke(app, ["connect", "code"], env={"HOMEBASE_SETTINGS_PATH": "settings.toml"})
        assert code_result.exit_code != 0
        assert "No such command 'code'" in code_result.output


def test_connect_help_for_managed_shows_code_only(monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        Path("settings.toml").write_text('role = "managed"\nnode_name = "app"\n', encoding="utf-8")
        app = load_app(monkeypatch, "settings.toml")
        result = runner.invoke(app, ["connect", "--help"], env={"HOMEBASE_SETTINGS_PATH": "settings.toml"})
        assert result.exit_code == 0
        scan_result = runner.invoke(app, ["connect", "scan"], env={"HOMEBASE_SETTINGS_PATH": "settings.toml"})
        assert scan_result.exit_code != 0
        assert "No such command 'scan'" in scan_result.output


def test_role_help_for_managed_shows_same_core_commands(monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        Path("settings.toml").write_text('role = "managed"\nnode_name = "app"\n', encoding="utf-8")
        app = load_app(monkeypatch, "settings.toml")
        result = runner.invoke(app, ["role", "--help"], env={"HOMEBASE_SETTINGS_PATH": "settings.toml"})
        assert result.exit_code == 0
        assert "list" in result.stdout
        assert "show" in result.stdout
        assert "edit" in result.stdout


def test_inventory_help_shows_show_and_edit(monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        env = {"HOMEBASE_SETTINGS_PATH": "settings.toml"}
        Path("settings.toml").write_text('role = "controller"\nnode_name = "control"\n', encoding="utf-8")
        app = load_app(monkeypatch, "settings.toml")
        result = runner.invoke(app, ["inventory", "--help"], env=env)
        assert result.exit_code == 0
        assert "show" in result.stdout
        assert "edit" in result.stdout


def test_dev_self_test_prints_success(monkeypatch) -> None:
    runner = CliRunner()
    app = load_app(monkeypatch)
    monkeypatch.setattr(
        "homebase_cli.cli.run_client_self_test",
        lambda: SelfTestResult(
            node_id="abc123",
            hostname="control",
            platform="Linux 6.1",
            version="0.1.0",
            paired_services=("ssh",),
            paired_ports=(22,),
        ),
    )
    result = runner.invoke(app, ["dev", "self-test"])
    assert result.exit_code == 0
    assert "local client self-test passed" in result.stdout
