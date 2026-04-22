"""Node lookup helpers backed by the local registry."""

from __future__ import annotations

from homebase_cli.registry import Node, child_nodes, find_node, load_nodes


def top_level_resources() -> tuple[Node, ...]:
    """Return top-level resources."""
    return child_nodes(None)


def find_resource(name: str) -> Node | None:
    """Find a resource by canonical name."""
    return find_node(name)


def child_resources(name: str | None = None) -> tuple[Node, ...]:
    """Return child resources for the given parent, or top-level resources."""
    if name is None:
        return top_level_resources()
    resource = find_resource(name)
    if resource is None:
        raise KeyError(f"unknown resource: {name}")
    return child_nodes(resource.name)


def all_resources() -> tuple[Node, ...]:
    """Return every registered node."""
    return load_nodes()
