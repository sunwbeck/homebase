from pathlib import Path

from homebase_cli.registry import (
    add_node,
    add_role_group,
    assign_node_role_group,
    link_role_group,
    load_nodes,
    load_role_groups,
    set_node_state,
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


def test_role_groups_and_node_state_persist_in_registry(tmp_path: Path) -> None:
    path = tmp_path / "nodes.toml"
    add_node(name="host", kind="host", path=path)
    add_node(name="host.app", parent="host", kind="vm", path=path)
    add_role_group(name="host-node", path=path)
    add_role_group(name="app-tier", path=path)
    link_role_group("host-node", "app-tier", path=path)
    assign_node_role_group("host.app", "app-tier", path=path)
    set_node_state("host.app", "status", "active", path=path)
    groups = load_role_groups(path)
    nodes = load_nodes(path)
    assert groups[0].name == "app-tier"
    assert groups[1].members == ("app-tier",)
    app_node = next(node for node in nodes if node.name == "host.app")
    assert app_node.role_groups == ("app-tier",)
    assert dict(app_node.states) == {"status": "active"}
