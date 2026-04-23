from pathlib import Path

from homebase_cli.registry import (
    Node,
    add_node,
    add_role_group,
    assign_node_role_group,
    link_role_group,
    load_nodes,
    load_role_groups,
    ensure_local_node,
    rename_node,
    save_nodes,
    set_node_runtime_role,
)


def test_add_node_persists_to_registry(tmp_path: Path) -> None:
    path = tmp_path / "nodes.toml"
    add_node(
        name="control",
        kind="control",
        address="192.168.0.10",
        runtime_hostname="controlpi",
        node_id="abc123",
        platform="Linux 6.1",
        client_port=8428,
        open_ports=(22, 8080),
        services=("ssh", "docker"),
        path=path,
    )
    nodes = load_nodes(path)
    assert len(nodes) == 1
    assert nodes[0].name == "control"
    assert nodes[0].runtime_hostname == "controlpi"
    assert nodes[0].node_id == "abc123"
    assert nodes[0].runtime_role == "controller"
    assert nodes[0].open_ports == (22, 8080)
    assert nodes[0].services == ("ssh", "docker")


def test_add_node_rejects_unknown_parent(tmp_path: Path) -> None:
    path = tmp_path / "nodes.toml"
    try:
        add_node(name="host.app", parent="host", kind="vm", path=path)
    except ValueError as exc:
        assert "unknown parent node" in str(exc)
    else:
        raise AssertionError("expected add_node to reject unknown parent")


def test_role_groups_persist_in_registry(tmp_path: Path) -> None:
    path = tmp_path / "nodes.toml"
    add_node(name="host", kind="host", path=path)
    add_node(name="host.app", parent="host", kind="vm", path=path)
    add_role_group(name="host-node", path=path)
    add_role_group(name="app-tier", path=path)
    link_role_group("host-node", "app-tier", path=path)
    assign_node_role_group("host.app", "app-tier", path=path)
    groups = load_role_groups(path)
    nodes = load_nodes(path)
    assert groups[0].name == "app-tier"
    assert groups[1].members == ("app-tier",)
    app_node = next(node for node in nodes if node.name == "host.app")
    assert app_node.role_groups == ("app-tier",)


def test_rename_and_runtime_role_update_persist_in_registry(tmp_path: Path) -> None:
    path = tmp_path / "nodes.toml"
    add_node(name="host", kind="host", path=path)
    add_node(name="host.app", parent="host", kind="vm", path=path)
    renamed = rename_node("host.app", "host.api", path=path)
    updated = set_node_runtime_role("host.api", "controller", path=path)
    nodes = load_nodes(path)
    assert renamed.name == "host.api"
    assert updated.runtime_role == "controller"
    api_node = next(node for node in nodes if node.name == "host.api")
    assert api_node.runtime_role == "controller"


def test_ensure_local_node_creates_and_renames_local_entry(tmp_path: Path) -> None:
    path = tmp_path / "nodes.toml"
    created = ensure_local_node("control", "controller", runtime_hostname="controlbox", path=path)
    renamed = ensure_local_node("workstation", "managed", runtime_hostname="wsbox", previous_name="control", path=path)
    nodes = load_nodes(path)
    assert created.name == "control"
    assert renamed.name == "workstation"
    assert renamed.runtime_role == "managed"
    assert next(node for node in nodes if node.name == "workstation").runtime_hostname == "wsbox"


def test_ensure_local_node_removes_stale_previous_entry_when_target_exists(tmp_path: Path) -> None:
    path = tmp_path / "nodes.toml"
    add_node(name="app", kind="controller", runtime_role="managed", runtime_hostname="control", path=path)
    add_node(name="control", kind="controller", runtime_role="controller", runtime_hostname="control", path=path)
    ensured = ensure_local_node(
        "control",
        "controller",
        runtime_hostname="control",
        previous_name="app",
        path=path,
    )
    nodes = load_nodes(path)
    assert ensured.name == "control"
    assert [node.name for node in nodes] == ["control"]


def test_save_nodes_escapes_service_record_backslashes(tmp_path: Path) -> None:
    path = tmp_path / "nodes.toml"
    save_nodes(
        (
            Node(
                name="pve",
                runtime_role="managed",
                service_records=(("example", "dead", None, "systemd", r"systemd-fsck@dev-disk-by\x2duuid-test"),),
            ),
        ),
        path=path,
    )
    loaded = load_nodes(path)
    assert loaded[0].service_records[0][4] == r"systemd-fsck@dev-disk-by\x2duuid-test"
