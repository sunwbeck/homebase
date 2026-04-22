import json
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from homebase_cli.cli import app
from homebase_cli.client import ClientProfile
from homebase_cli.packaging import PackageRecord
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


def test_init_interactive_can_add_role_and_package_location() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(
            app,
            ["init"],
            env={"HOMEBASE_SETTINGS_PATH": "settings.toml"},
            input="3\nbuilder\n/tmp/packages\n",
        )
        assert result.exit_code == 0
        assert "Set local role to builder" in result.stdout
        assert "/tmp/packages" in result.stdout


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


def test_package_location_set_sets_persistent_directory() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(
            app,
            ["package", "location", "set", "/tmp/packages"],
            env={"HOMEBASE_SETTINGS_PATH": "settings.toml"},
        )
        assert result.exit_code == 0
        assert "/tmp/packages" in result.stdout


def test_package_location_show_prints_directory() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        Path("settings.toml").write_text('package_location = "/tmp/packages"\nroles = ["control", "client"]\n', encoding="utf-8")
        result = runner.invoke(app, ["package", "location", "show"], env={"HOMEBASE_SETTINGS_PATH": "settings.toml"})
        assert result.exit_code == 0
        assert "/tmp/packages" in result.stdout


def test_package_list_prints_recorded_packages(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setattr(
        "homebase_cli.cli.installable_packages",
        lambda dist_dir=None: (
            PackageRecord(
                filename="homebase_cli-0.1.0-py3-none-any.whl",
                version="0.1.0",
                message="pairing fix",
                created_at="2026-04-17T10:00:00+00:00",
            ),
        ),
    )
    result = runner.invoke(app, ["package", "list", "--dist-dir", "/tmp/packages"])
    assert result.exit_code == 0
    assert "pairing fix" in result.stdout
    assert "Wheel File" in result.stdout


def test_package_build_skips_when_same_version_exists(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setattr("homebase_cli.cli._is_interactive", lambda: True)
    monkeypatch.setattr("homebase_cli.cli.project_version", lambda: "0.1.1")
    monkeypatch.setattr(
        "homebase_cli.cli.built_wheels",
        lambda dist_dir=None: (Path("/tmp/packages/homebase_cli-0.1.1-py3-none-any.whl"),),
    )
    called = {"value": False}

    def fake_build_wheel(*args, **kwargs):
        called["value"] = True
        return Path("/tmp/packages/homebase_cli-0.1.1-py3-none-any.whl")

    monkeypatch.setattr("homebase_cli.cli.build_wheel", fake_build_wheel)
    result = runner.invoke(app, ["package", "build", "--dist-dir", "/tmp/packages"], input="3\n")
    assert result.exit_code == 0
    assert "Skipped build." in result.stdout
    assert called["value"] is False


def test_package_build_can_create_duplicate_when_same_version_exists(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setattr("homebase_cli.cli._is_interactive", lambda: True)
    monkeypatch.setattr("homebase_cli.cli.project_version", lambda: "0.1.1")
    monkeypatch.setattr(
        "homebase_cli.cli.built_wheels",
        lambda dist_dir=None: (Path("/tmp/packages/homebase_cli-0.1.1-py3-none-any.whl"),),
    )
    monkeypatch.setattr("homebase_cli.cli.record_package", lambda wheel_path, message="": None)
    monkeypatch.setattr(
        "homebase_cli.cli.build_duplicate_wheel",
        lambda dist_dir=None, on_tick=None: Path("/tmp/packages/homebase_cli-0.1.1-py3-none-any-20260420T000000Z.whl"),
    )
    result = runner.invoke(app, ["package", "build", "--dist-dir", "/tmp/packages", "--message", "dup"], input="2\n")
    assert result.exit_code == 0
    assert "Built package" in result.stdout
    assert "20260420T000000Z" in result.stdout


def test_package_publish_prints_published_paths_and_bootstrap_command(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setattr("homebase_cli.cli.built_wheels", lambda dist_dir=None: (Path("/mnt/files/homebase/dist/homebase_cli-0.1.0-py3-none-any.whl"),))
    monkeypatch.setattr(
        "homebase_cli.cli.publish_bootstrap",
        lambda dist_dir=None, on_tick=None: (
            Path("/mnt/files/homebase/bootstrap/bootstrap-homebase.sh"),
            Path("/mnt/files/homebase/bootstrap/homebase-cli-source.tar.gz"),
        ),
    )
    result = runner.invoke(app, ["package", "publish", "--dist-dir", "/mnt/files/homebase/dist"])
    assert result.exit_code == 0
    assert "/mnt/files/homebase/bootstrap/bootstrap-homebase.sh" in result.stdout
    normalized = " ".join(result.stdout.split())
    assert "Fresh-node install command:" in result.stdout
    assert "bash /mnt/files/homebase/bootstrap/bootstrap-homebase.sh" in normalized
    assert "bash /mnt/files/homebase/bootstrap/bootstrap-homebase.sh --wheel-dir /mnt/files/homebase/dist" in normalized
    assert "bash /mnt/files/homebase/bootstrap/bootstrap-homebase.sh --package-location /mnt/files/homebase/dist" in normalized
    assert "bash /mnt/files/homebase/bootstrap/bootstrap-homebase.sh --role client" in normalized


def test_package_install_command_uses_given_wheel() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        wheel_path = Path("homebase_cli-0.1.0-py3-none-any.whl")
        wheel_path.write_text("placeholder", encoding="utf-8")
        result = runner.invoke(
            app,
            [
                "package",
                "install-command",
                "--wheel",
                str(wheel_path),
                "--python",
                "python3",
            ],
        )
        assert result.exit_code == 0
        assert "python3 -m pip install --upgrade" in result.stdout
        assert "homebase_cli-0.1.0-py3-none-any.whl" in result.stdout


def test_package_delete_uses_selected_wheel(monkeypatch) -> None:
    runner = CliRunner()
    deleted: dict[str, object] = {}
    monkeypatch.setattr(
        "homebase_cli.cli.installable_packages",
        lambda dist_dir=None: (
            PackageRecord(
                filename="homebase_cli-0.1.1-py3-none-any.whl",
                version="0.1.1",
                message="pairing fix",
                created_at="2026-04-20T00:00:00+00:00",
            ),
        ),
    )
    monkeypatch.setattr(
        "homebase_cli.cli.resolve_wheel",
        lambda dist_dir=None, wheel_name=None: Path("/tmp/packages/homebase_cli-0.1.1-py3-none-any.whl"),
    )
    monkeypatch.setattr(
        "homebase_cli.cli.delete_package",
        lambda wheel_path, dist_dir=None: deleted.update({"wheel": wheel_path, "dist_dir": dist_dir}),
    )
    result = runner.invoke(app, ["package", "delete", "--dist-dir", "/tmp/packages"], input="1\n")
    assert result.exit_code == 0
    assert "Deleted package" in result.stdout
    assert deleted["wheel"] == Path("/tmp/packages/homebase_cli-0.1.1-py3-none-any.whl")


def test_package_install_command_uses_recovery_wheel_when_package_dir_is_empty(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setattr("homebase_cli.cli.resolve_wheel", lambda dist_dir=None, wheel=None: Path("/tmp/recovery/current.whl"))
    monkeypatch.setattr("homebase_cli.cli.built_wheels", lambda dist_dir=None: ())
    monkeypatch.setattr("homebase_cli.cli.latest_recovery_wheel", lambda: Path("/tmp/recovery/current.whl"))
    result = runner.invoke(app, ["package", "install-command"])
    assert result.exit_code == 0
    assert "/tmp/recovery/current.whl" in result.stdout
    assert "Using locally preserved recovery wheel" in result.stdout


def test_package_install_runs_selected_wheel(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setattr("homebase_cli.cli.resolve_wheel", lambda dist_dir=None, wheel_name=None: Path("/tmp/builds/homebase_cli.whl"))
    monkeypatch.setattr(
        "homebase_cli.cli.install_wheel",
        lambda wheel_path, python_bin=None, on_tick=None: SimpleNamespace(returncode=0, stdout="installed\n", stderr="", log_path=Path("/tmp/install.log")),
    )
    result = runner.invoke(app, ["package", "install", "--wheel", "homebase_cli.whl"])
    assert result.exit_code == 0
    assert "Installed wheel" in result.stdout


def test_package_install_prompts_before_progress(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setattr(
        "homebase_cli.cli.installable_packages",
        lambda dist_dir=None: (
            PackageRecord(
                filename="homebase_cli-0.1.1-py3-none-any.whl",
                version="0.1.1",
                message="test2",
                created_at="2026-04-20T00:00:00+00:00",
            ),
        ),
    )
    monkeypatch.setattr(
        "homebase_cli.cli.resolve_wheel",
        lambda dist_dir=None, wheel_name=None: Path("/tmp/builds/homebase_cli-0.1.1-py3-none-any.whl"),
    )
    monkeypatch.setattr(
        "homebase_cli.cli.install_wheel",
        lambda wheel_path, python_bin=None, on_tick=None: SimpleNamespace(returncode=0, stdout="installed\n", stderr="", log_path=Path("/tmp/install.log")),
    )
    result = runner.invoke(app, ["package", "install", "--dist-dir", "/tmp/builds"], input="1\n")
    assert result.exit_code == 0
    assert "Select number" in result.stdout
    assert "1/2 Resolve package to install" in result.stdout


def test_package_install_can_target_explicit_python(monkeypatch) -> None:
    runner = CliRunner()
    seen: dict[str, object] = {}
    monkeypatch.setattr("homebase_cli.cli.resolve_wheel", lambda dist_dir=None, wheel_name=None: Path("/tmp/builds/homebase_cli.whl"))
    monkeypatch.setattr(
        "homebase_cli.cli.install_wheel",
        lambda wheel_path, python_bin=None, on_tick=None: seen.update({"python_bin": python_bin}) or SimpleNamespace(returncode=0, stdout="installed\n", stderr="", log_path=Path("/tmp/install.log")),
    )
    result = runner.invoke(app, ["package", "install", "--wheel", "homebase_cli.whl", "--python", "/tmp/custom/bin/python"])
    assert result.exit_code == 0
    assert seen == {"python_bin": "/tmp/custom/bin/python"}
    assert "Installed into Python: /tmp/custom/bin/python" in result.stdout


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
