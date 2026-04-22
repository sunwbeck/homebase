from pathlib import Path

from homebase_cli.packaging import (
    BOOTSTRAP_DIRNAME,
    BOOTSTRAP_SCRIPT_NAME,
    DEFAULT_DIST_DIR,
    PackageRecord,
    RECOVERY_DIR,
    SOURCE_BUNDLE_NAME,
    bootstrap_publish_dir,
    delete_package,
    install_command,
    install_wheel,
    latest_recovery_wheel,
    preserve_recovery_wheel,
    resolve_dist_dir,
)


def test_install_command_quotes_python_and_wheel_path() -> None:
    wheel_path = Path("/tmp/homebase cli/homebase_cli-0.1.0-py3-none-any.whl")
    command = install_command(wheel_path, python_bin="/usr/bin/python3")
    assert command == "/usr/bin/python3 -m pip install --upgrade '/tmp/homebase cli/homebase_cli-0.1.0-py3-none-any.whl'"


def test_resolve_dist_dir_uses_default_when_not_provided(monkeypatch) -> None:
    monkeypatch.setenv("HOMEBASE_SETTINGS_PATH", "/tmp/nonexistent-homebase-settings.toml")
    assert resolve_dist_dir() == DEFAULT_DIST_DIR


def test_package_record_label_includes_message() -> None:
    record = PackageRecord(
        filename="homebase_cli-0.1.0-py3-none-any.whl",
        version="0.1.0",
        message="client pairing fix",
        created_at="2026-04-17T10:00:00+00:00",
    )
    assert record.label == "0.1.0 | homebase_cli-0.1.0-py3-none-any.whl | client pairing fix"


def test_bootstrap_publish_dir_is_parent_bootstrap() -> None:
    dist_dir = Path("/mnt/files/homebase/dist")
    assert bootstrap_publish_dir(dist_dir) == Path("/mnt/files/homebase") / BOOTSTRAP_DIRNAME
    assert BOOTSTRAP_SCRIPT_NAME.endswith(".sh")
    assert SOURCE_BUNDLE_NAME.endswith(".tar.gz")


def test_preserve_recovery_wheel_keeps_current_copy(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "homebase_cli-0.1.0-py3-none-any.whl"
    source.write_text("wheel", encoding="utf-8")
    monkeypatch.setattr("homebase_cli.packaging.RECOVERY_DIR", tmp_path / "recovery")
    preserved = preserve_recovery_wheel(source)
    assert preserved.exists()
    assert (tmp_path / "recovery" / "current.whl").exists()


def test_delete_package_removes_file_and_index_entry(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    wheel_path = dist_dir / "homebase_cli-0.1.1-py3-none-any.whl"
    wheel_path.write_text("wheel", encoding="utf-8")
    (dist_dir / "packages.json").write_text(
        '[{"filename":"homebase_cli-0.1.1-py3-none-any.whl","version":"0.1.1","message":"test","created_at":"2026-04-20T00:00:00+00:00"}]\n',
        encoding="utf-8",
    )

    delete_package(wheel_path, dist_dir)

    assert not wheel_path.exists()
    assert "homebase_cli-0.1.1-py3-none-any.whl" not in (dist_dir / "packages.json").read_text(encoding="utf-8")


def test_install_wheel_targets_current_environment_by_default(tmp_path: Path, monkeypatch) -> None:
    wheel_path = tmp_path / "homebase_cli-0.1.1-py3-none-any.whl"
    wheel_path.write_text("wheel", encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "homebase_cli.packaging._run_logged",
        lambda args, cwd, log_prefix, on_tick=None: calls.append(args) or type(
            "Result",
            (),
            {"returncode": 0, "stdout": "", "stderr": "", "log_path": Path("/tmp/install.log")},
        )(),
    )
    monkeypatch.setattr("homebase_cli.packaging.preserve_recovery_wheel", lambda wheel_path: wheel_path)
    monkeypatch.setattr("homebase_cli.packaging.sys.executable", "/tmp/current/bin/python")

    result = install_wheel(wheel_path)

    assert result.returncode == 0
    assert calls == [["/tmp/current/bin/python", "-m", "pip", "install", "--upgrade", str(wheel_path)]]


def test_install_wheel_uses_explicit_python_without_relinking(tmp_path: Path, monkeypatch) -> None:
    wheel_path = tmp_path / "homebase_cli-0.1.1-py3-none-any.whl"
    wheel_path.write_text("wheel", encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "homebase_cli.packaging._run_logged",
        lambda args, cwd, log_prefix, on_tick=None: calls.append(args) or type(
            "Result",
            (),
            {"returncode": 0, "stdout": "", "stderr": "", "log_path": Path("/tmp/install.log")},
        )(),
    )
    monkeypatch.setattr("homebase_cli.packaging.preserve_recovery_wheel", lambda wheel_path: wheel_path)

    result = install_wheel(wheel_path, python_bin="/tmp/custom/bin/python")

    assert result.returncode == 0
    assert calls == [["/tmp/custom/bin/python", "-m", "pip", "install", "--upgrade", str(wheel_path)]]
