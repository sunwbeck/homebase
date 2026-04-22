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
        roles_result = runner.invoke(app, ["roles"], env={"HOMEBASE_SETTINGS_PATH": "settings.toml"})
        assert "builder" in roles_result.stdout


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


def test_role_command_prints_current_role() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        Path("settings.toml").write_text('role = "client"\n', encoding="utf-8")
        result = runner.invoke(app, ["role"], env={"HOMEBASE_SETTINGS_PATH": "settings.toml"})
        assert result.exit_code == 0
        assert "client" in result.stdout


def test_roles_command_adds_role() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(app, ["roles", "--add", "builder"], env={"HOMEBASE_SETTINGS_PATH": "settings.toml"})
        assert result.exit_code == 0
        assert "builder" in result.stdout


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
    assert "Version" in result.stdout


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


def test_package_upgrade_uses_latest_github_version(monkeypatch) -> None:
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
    result = runner.invoke(app, ["package", "upgrade"])
    assert result.exit_code == 0
    assert "Selected latest target: v0.1.2" in result.stdout


def test_package_update_alias_calls_upgrade(monkeypatch) -> None:
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
