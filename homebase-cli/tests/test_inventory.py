from pathlib import Path

from homebase_cli.inventory import render_ansible_inventory
from homebase_cli.registry import add_node, load_nodes


def test_render_ansible_inventory_uses_registered_nodes(tmp_path: Path) -> None:
    path = tmp_path / "nodes.toml"
    add_node(name="control", kind="control", address="192.168.219.10", ssh_user="sun", path=path)
    inventory = render_ansible_inventory(load_nodes(path))
    assert "[homebase]" in inventory
    assert "control ansible_host=192.168.219.10 ansible_user=sun" in inventory
