import json
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from homebase_cli.cli import app
from homebase_cli.client import ClientProfile
from homebase_cli.selftest import SelfTestResult


def test_node_scan_updates_discovery_cache(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setattr("homebase_cli.cli.detect_scannable_networks", lambda: ("192.168.0.0/24",))
    monkeypatch.setattr("homebase_cli.cli.scan_for_clients", lambda cidr, port, timeout: ())
    with runner.isolated_filesystem():
        result = runner.invoke(
            app,
            ["node", "scan"],
            env={"HOMEBASE_DISCOVERY_PATH": "discovered.json", "HOMEBASE_REGISTRY_PATH": "nodes.toml"},
        )
        assert result.exit_code == 0
        assert "No homebase clients found" in result.stdout
        payload = json.loads(open("discovered.json", encoding="utf-8").read())
        assert payload == []


def test_node_add_uses_cached_discovery_and_pairing(monkeypatch) -> None:
    runner = CliRunner()
    discovered = [
        {
            "address": "192.168.0.20",
            "port": 8428,
            "discovery": {
                "node_id": "abc123",
                "hostname": "app",
                "platform": "Linux 6.1",
                "version": "0.1.0",
                "pairing_required": True,
            },
        }
    ]
    monkeypatch.setattr(
        "homebase_cli.cli.pair_with_client",
        lambda address, code, port=8428: ClientProfile(
            node_id="abc123",
            hostname="app",
            platform="Linux 6.1",
            version="0.1.0",
            open_ports=(22, 8080),
            services=("ssh", "docker"),
        ) if code == "12345678" else None,
    )
    with runner.isolated_filesystem():
        with open("discovered.json", "w", encoding="utf-8") as handle:
            json.dump(discovered, handle)
        result = runner.invoke(
            app,
            ["node", "add", "host.app"],
            env={"HOMEBASE_DISCOVERY_PATH": "discovered.json", "HOMEBASE_REGISTRY_PATH": "nodes.toml"},
            input="1\n12345678\n1\n4\nsun\napp vm\n",
        )
        assert result.exit_code == 0
        assert "Registered node host.app" in result.stdout


def test_node_add_fails_when_pairing_code_is_wrong(monkeypatch) -> None:
    runner = CliRunner()
    discovered = [
        {
            "address": "192.168.0.20",
            "port": 8428,
            "discovery": {
                "node_id": "abc123",
                "hostname": "app",
                "platform": "Linux 6.1",
                "version": "0.1.0",
                "pairing_required": True,
            },
        }
    ]
    monkeypatch.setattr("homebase_cli.cli.pair_with_client", lambda address, code, port=8428: None)
    with runner.isolated_filesystem():
        with open("discovered.json", "w", encoding="utf-8") as handle:
            json.dump(discovered, handle)
        result = runner.invoke(
            app,
            ["node", "add", "host.app"],
            env={"HOMEBASE_DISCOVERY_PATH": "discovered.json", "HOMEBASE_REGISTRY_PATH": "nodes.toml"},
            input="1\n12345678\n",
        )
        assert result.exit_code != 0
        assert "pairing failed" in result.output


def test_init_sets_role() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(app, ["init", "--role", "control"], env={"HOMEBASE_SETTINGS_PATH": "settings.toml"})
        assert result.exit_code == 0
        assert "Set local role to control" in result.stdout


def test_init_adds_unknown_role_when_given_directly() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(app, ["init", "--role", "builder"], env={"HOMEBASE_SETTINGS_PATH": "settings.toml"})
        assert result.exit_code == 0
        assert 'role = "builder"' in Path("settings.toml").read_text(encoding="utf-8")


def test_init_interactive_can_add_role() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(
            app,
            ["init"],
            env={"HOMEBASE_SETTINGS_PATH": "settings.toml"},
            input="3\nbuilder\n",
        )
        assert result.exit_code == 0
        assert "Set local role to builder" in result.stdout


def test_role_templates_command_lists_skeletons() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["role", "templates"])
    assert result.exit_code == 0
    assert "host" in result.stdout
    assert "fleet" in result.stdout


def test_role_group_commands_build_hierarchy() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        env = {"HOMEBASE_SETTINGS_PATH": "settings.toml", "HOMEBASE_REGISTRY_PATH": "nodes.toml"}
        Path("settings.toml").write_text('role = "control"\nroles = ["control", "client"]\n', encoding="utf-8")
        Path("nodes.toml").write_text(
            '[[nodes]]\nname = "host"\nkind = "host"\n\n[[nodes]]\nname = "host.app"\nparent = "host"\nkind = "vm"\n',
            encoding="utf-8",
        )
        assert runner.invoke(app, ["role", "add", "host-node", "--template", "host"], env=env).exit_code == 0
        assert runner.invoke(app, ["role", "add", "app-tier", "--template", "service"], env=env).exit_code == 0
        assert runner.invoke(app, ["role", "link", "host-node", "app-tier"], env=env).exit_code == 0
        assert runner.invoke(app, ["role", "assign", "host.app", "app-tier"], env=env).exit_code == 0
        status_result = runner.invoke(app, ["role", "status", "host.app"], env=env)
        assert status_result.exit_code == 0
        assert "app-tier" in status_result.stdout
        tree_result = runner.invoke(app, ["role", "status"], env=env)
        assert "host-node" in tree_result.stdout
        assert "app-tier" in tree_result.stdout


def test_state_commands_store_values() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        env = {"HOMEBASE_SETTINGS_PATH": "settings.toml", "HOMEBASE_REGISTRY_PATH": "nodes.toml"}
        Path("nodes.toml").write_text('[[nodes]]\nname = "host.app"\nkind = "vm"\n', encoding="utf-8")
        assert runner.invoke(app, ["state", "set", "host.app", "site", "home"], env=env).exit_code == 0
        assert runner.invoke(app, ["state", "set", "host.app", "status", "active"], env=env).exit_code == 0
        show_result = runner.invoke(app, ["state", "show", "host.app"], env=env)
        assert show_result.exit_code == 0
        assert "site" in show_result.stdout
        assert "active" in show_result.stdout
        assert runner.invoke(app, ["state", "unset", "host.app", "status"], env=env).exit_code == 0


def test_package_versions_prints_github_versions(monkeypatch) -> None:
    runner = CliRunner()
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


def test_package_version_alias_prints_github_versions(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setattr(
        "homebase_cli.cli.github_versions",
        lambda repo_url, include_prerelease=False: (
            SimpleNamespace(version="main", source="branch", published_at="", summary="default branch"),
        ),
    )
    result = runner.invoke(app, ["package", "version"])
    assert result.exit_code == 0
    assert "default branch" in result.stdout


def test_package_status_prints_local_install_state(monkeypatch) -> None:
    runner = CliRunner()
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
        lambda repo_url: SimpleNamespace(version="v0.1.2", summary="latest note"),
    )
    result = runner.invoke(app, ["package", "status"])
    assert result.exit_code == 0
    assert "installed version: 0.1.1" in result.stdout
    assert "latest available: v0.1.2" in result.stdout


def test_package_status_remote_uses_registered_node(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setattr(
        "homebase_cli.cli.find_resource",
        lambda resource: SimpleNamespace(address="192.168.1.10", client_port=8428),
    )
    monkeypatch.setattr(
        "homebase_cli.cli.fetch_package_status",
        lambda address, port=8428: {"installed_version": "0.1.1", "requested_ref": "v0.1.1", "resolved_ref": "abc123"},
    )
    with runner.isolated_filesystem():
        Path("settings.toml").write_text('role = "control"\nroles = ["control", "client"]\n', encoding="utf-8")
        result = runner.invoke(app, ["package", "status", "host.app"], env={"HOMEBASE_SETTINGS_PATH": "settings.toml"})
        assert result.exit_code == 0
        assert "Remote package status: host.app" in result.stdout
        assert "installed version: 0.1.1" in result.stdout


def test_package_install_prompts_for_github_version_before_progress(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setattr(
        "homebase_cli.cli.github_versions",
        lambda repo_url, include_prerelease=False: (
            SimpleNamespace(ref="v0.1.1", version="v0.1.1", summary="test2", label="v0.1.1 | test2"),
        ),
    )
    monkeypatch.setattr(
        "homebase_cli.cli.install_github_ref",
        lambda ref, repo_url, python_bin=None, summary=None, on_tick=None: (
            SimpleNamespace(returncode=0, stdout="installed\n", stderr="", log_path=Path("/tmp/install.log")),
            SimpleNamespace(installed_version="0.1.1", requested_ref=ref, resolved_ref="abc123"),
        ),
    )
    result = runner.invoke(app, ["package", "install"], input="1\n")
    assert result.exit_code == 0
    assert "Select number" in result.stdout
    assert "1/3 Resolve GitHub target" in result.stdout


def test_package_install_can_target_explicit_python(monkeypatch) -> None:
    runner = CliRunner()
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


def test_package_install_remote_requests_install(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setattr(
        "homebase_cli.cli.find_resource",
        lambda resource: SimpleNamespace(address="192.168.1.10", client_port=8428),
    )
    monkeypatch.setattr(
        "homebase_cli.cli.request_package_install",
        lambda address, ref, repo_url, summary=None, port=8428: {
            "installed_version": "0.1.1",
            "requested_ref": ref,
            "resolved_ref": "abc123",
        },
    )
    with runner.isolated_filesystem():
        Path("settings.toml").write_text('role = "control"\nroles = ["control", "client"]\n', encoding="utf-8")
        result = runner.invoke(
            app,
            ["package", "install", "host.app", "--ref", "v0.1.1"],
            env={"HOMEBASE_SETTINGS_PATH": "settings.toml"},
        )
        assert result.exit_code == 0
        assert "Remote install completed: host.app" in result.stdout
        assert "requested ref: v0.1.1" in result.stdout


def test_package_update_uses_latest_github_version(monkeypatch) -> None:
    runner = CliRunner()
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


def test_package_update_remote_requests_update(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setattr(
        "homebase_cli.cli.latest_github_version",
        lambda repo_url, include_prerelease=False: SimpleNamespace(ref="v0.1.2", version="v0.1.2", summary="latest note"),
    )
    monkeypatch.setattr(
        "homebase_cli.cli.find_resource",
        lambda resource: SimpleNamespace(address="192.168.1.10", client_port=8428),
    )
    monkeypatch.setattr(
        "homebase_cli.cli.request_package_upgrade",
        lambda address, repo_url, include_prerelease=False, port=8428: {
            "installed_version": "0.1.2",
            "requested_ref": "v0.1.2",
            "resolved_ref": "def456",
        },
    )
    with runner.isolated_filesystem():
        Path("settings.toml").write_text('role = "control"\nroles = ["control", "client"]\n', encoding="utf-8")
        result = runner.invoke(app, ["package", "update", "host.app"], env={"HOMEBASE_SETTINGS_PATH": "settings.toml"})
        assert result.exit_code == 0
        assert "Remote update completed: host.app" in result.stdout
        assert "requested ref: v0.1.2" in result.stdout


def test_root_help_uses_workflow_order() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    output = result.stdout
    command_lines = [line for line in output.splitlines() if line.strip().startswith(("│ init", "│ role", "│ state", "│ client", "│ node", "│ package", "│ dev"))]
    assert command_lines == [
        "│ init      Initialize the local node role for this homebase installation.     │",
        "│ role      Define local role groups and current-node memberships.             │",
        "│ state     Store and inspect current-node state values.                       │",
        "│ client    Run the homebase client service on one managed node.               │",
        "│ node      Scan for clients and inspect registered nodes.                     │",
        "│ package   Check installed homebase revisions and install or update from      │",
        "│ dev       Development and internal commands.                                 │",
    ]
    assert "docs" not in output
    assert "ansible" not in output
    assert "status" not in output
    assert " ls " not in output
    assert " info " not in output


def test_package_help_uses_workflow_order() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["package", "--help"])
    assert result.exit_code == 0
    output = result.stdout
    command_lines = [line for line in output.splitlines() if line.strip().startswith(("│ status", "│ versions", "│ update", "│ install"))]
    assert command_lines == [
        "│ status     Show the currently installed homebase revision on this node or    │",
        "│ versions   List installable GitHub refs with short release notes.            │",
        "│ update     Update to the latest GitHub release, or default branch when no    │",
        "│ install    Install one GitHub ref, or choose a version interactively.        │",
    ]
    assert "upgrade" not in output


def test_dev_help_includes_internal_commands() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["dev", "--help"])
    assert result.exit_code == 0
    output = result.stdout
    assert "self-test" in output
    assert "docs" in output
    assert "ansible" in output


def test_node_help_uses_registry_workflow_order() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["node", "--help"])
    assert result.exit_code == 0
    output = result.stdout
    command_lines = [line for line in output.splitlines() if line.strip().startswith(("│ scan", "│ add", "│ status", "│ ls", "│ info"))]
    assert command_lines == [
        "│ scan     Scan a local network for homebase clients and update the discovery  │",
        "│ add      Add one node to the persistent local registry, preferably from      │",
        "│ status   Show currently registered nodes.                                    │",
        "│ ls       List top-level or child resources.                                  │",
        "│ info     Show high-level information for a resource.                         │",
    ]


def test_role_help_uses_group_management_workflow() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["role", "--help"])
    assert result.exit_code == 0
    output = result.stdout
    assert "templates" in output
    assert "list" in output
    assert "add" in output
    assert "link" in output
    assert "assign" in output
    assert "unassign" in output


def test_state_help_uses_simple_state_commands() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["state", "--help"])
    assert result.exit_code == 0
    output = result.stdout
    assert "show" in output
    assert "set" in output
    assert "unset" in output


def test_client_commands_require_client_role() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        Path("settings.toml").write_text('role = "control"\n', encoding="utf-8")
        result = runner.invoke(app, ["client", "code"], env={"HOMEBASE_SETTINGS_PATH": "settings.toml"})
        assert result.exit_code != 0
        assert "current role is control" in result.output


def test_dev_self_test_prints_success(monkeypatch) -> None:
    runner = CliRunner()
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
