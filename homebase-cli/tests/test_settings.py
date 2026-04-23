from pathlib import Path

from homebase_cli.settings import Settings, load_settings, runtime_roles, save_settings, set_package_location, set_role


def test_save_and_load_settings_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    save_settings(Settings(role="control", package_location="/tmp/packages"), path)
    loaded = load_settings(path)
    assert loaded.role == "control"
    assert loaded.package_location == "/tmp/packages"


def test_setters_preserve_other_values(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    set_role("managed", path)
    set_package_location("/mnt/files/homebase/dist", path)
    loaded = load_settings(path)
    assert loaded.role == "managed"
    assert loaded.package_location == "/mnt/files/homebase/dist"


def test_set_role_normalizes_client_to_managed(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    set_role("client", path)
    loaded = load_settings(path)
    assert loaded.role == "managed"


def test_runtime_roles_are_fixed() -> None:
    assert runtime_roles() == ("control", "managed")
