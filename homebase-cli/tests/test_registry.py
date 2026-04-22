from pathlib import Path

from homebase_cli.registry import add_node, load_nodes


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
