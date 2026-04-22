from pathlib import Path

from homebase_cli.settings import Settings, add_role, load_settings, save_settings, set_package_location, set_role


def test_save_and_load_settings_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    save_settings(Settings(role="control", package_location="/tmp/packages"), path)
    loaded = load_settings(path)
    assert loaded.role == "control"
    assert loaded.package_location == "/tmp/packages"


def test_setters_preserve_other_values(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    set_role("client", path)
    set_package_location("/mnt/files/homebase/dist", path)
    loaded = load_settings(path)
    assert loaded.role == "client"
    assert loaded.package_location == "/mnt/files/homebase/dist"


def test_add_role_persists_custom_role(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    add_role("builder", path)
    loaded = load_settings(path)
    assert "builder" in loaded.roles
