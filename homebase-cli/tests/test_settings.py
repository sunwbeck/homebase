from pathlib import Path

from homebase_cli.settings import (
    RoleGroup,
    Settings,
    add_group,
    assign_group,
    link_group,
    list_group_memberships,
    list_groups,
    list_states,
    load_settings,
    save_settings,
    set_package_location,
    set_role,
    set_state_value,
    unset_state_value,
)


def test_save_and_load_settings_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    save_settings(
        Settings(
            role="control",
            package_location="/tmp/packages",
            groups=(RoleGroup(name="host-node", template="host", description="physical host", members=("nas-vm", "app-vm")),),
            group_memberships=("host-node",),
            states=(("site", "home"), ("status", "active")),
        ),
        path,
    )
    loaded = load_settings(path)
    assert loaded.role == "control"
    assert loaded.package_location == "/tmp/packages"
    assert loaded.groups[0].name == "host-node"
    assert loaded.group_memberships == ("host-node",)
    assert dict(loaded.states) == {"site": "home", "status": "active"}


def test_setters_preserve_other_values(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    set_role("client", path)
    set_package_location("/mnt/files/homebase/dist", path)
    loaded = load_settings(path)
    assert loaded.role == "client"
    assert loaded.package_location == "/mnt/files/homebase/dist"


def test_group_helpers_build_hierarchy_and_assignments(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    add_group("host-node", template="host", path=path)
    add_group("app-vm", template="node", path=path)
    link_group("host-node", "app-vm", path=path)
    assign_group("host-node", path=path)
    groups = list_groups(path)
    memberships = list_group_memberships(path)
    assert groups[0].members == ("app-vm",)
    assert memberships == ("host-node",)


def test_state_helpers_store_and_remove_values(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    set_state_value("site", "home", path)
    set_state_value("status", "active", path)
    assert dict(list_states(path)) == {"site": "home", "status": "active"}
    unset_state_value("status", path)
    assert dict(list_states(path)) == {"site": "home"}
