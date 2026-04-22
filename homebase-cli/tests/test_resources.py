from pathlib import Path

from homebase_cli.registry import add_node, child_nodes, find_node


def test_find_node_returns_registered_entry(tmp_path: Path) -> None:
    path = tmp_path / "nodes.toml"
    add_node(name="host", kind="host", path=path)
    add_node(name="host.app", parent="host", kind="vm", path=path)
    resource = find_node("host.app", path=path)
    assert resource is not None
    assert resource.kind == "vm"
    assert resource.parent == "host"


def test_child_nodes_of_host(tmp_path: Path) -> None:
    path = tmp_path / "nodes.toml"
    add_node(name="host", kind="host", path=path)
    add_node(name="host.storage", parent="host", kind="vm", path=path)
    add_node(name="host.app", parent="host", kind="vm", path=path)
    children = child_nodes("host", path=path)
    assert [item.name for item in children] == [
        "host.app",
        "host.storage",
    ]
