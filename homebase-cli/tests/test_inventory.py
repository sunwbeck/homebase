from pathlib import Path

from homebase_cli.inventory import render_ansible_inventory
from homebase_cli.registry import add_node, add_role_group, assign_node_role_group, load_nodes, load_role_groups


def test_render_ansible_inventory_uses_registered_nodes(tmp_path: Path) -> None:
    path = tmp_path / "nodes.toml"
    add_node(name="control", kind="control", address="192.168.219.10", ssh_user="sun", path=path)
    add_role_group(name="control-plane", path=path)
    assign_node_role_group("control", "control-plane", path=path)
    inventory = render_ansible_inventory(load_nodes(path), load_role_groups(path))
    assert "homebase:" in inventory
    assert "control:" in inventory
    assert "ansible_host: 192.168.219.10" in inventory
    assert "ansible_user: sun" in inventory
    assert "homebase_role: control" in inventory
    assert "homebase_groups:" in inventory
    assert "homebase_state:" not in inventory
