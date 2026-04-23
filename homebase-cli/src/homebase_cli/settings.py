"""Persistent local settings for one homebase installation."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import tomllib


DEFAULT_SETTINGS_PATH = Path.home() / ".config" / "homebase" / "settings.toml"
DEFAULT_RUNTIME_ROLES = ("control", "client")
ROLE_TEMPLATES: tuple[tuple[str, str], ...] = (
    ("node", "Single machine or VM."),
    ("host", "Physical host that contains child nodes."),
    ("group", "Generic collection of nodes or sub-groups."),
    ("fleet", "Rollout or policy group spanning multiple nodes."),
    ("service", "Functional group such as app, storage, or llm."),
    ("custom", "User-defined grouping with no preset behavior."),
)


@dataclass(frozen=True)
class RoleGroup:
    """One named local role group."""

    name: str
    template: str = "custom"
    description: str | None = None
    members: tuple[str, ...] = ()


@dataclass(frozen=True)
class Settings:
    """Persistent local settings for one homebase installation."""

    role: str | None = None
    package_location: str | None = None
    roles: tuple[str, ...] = DEFAULT_RUNTIME_ROLES
    groups: tuple[RoleGroup, ...] = ()
    group_memberships: tuple[str, ...] = ()
    states: tuple[tuple[str, str], ...] = ()


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _normalize_name(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized:
        raise ValueError("name cannot be empty")
    return normalized


def settings_path(path: Path | None = None) -> Path:
    """Resolve the active settings path."""
    if path is not None:
        return path
    override = os.environ.get("HOMEBASE_SETTINGS_PATH")
    if override:
        return Path(override)
    return DEFAULT_SETTINGS_PATH


def load_settings(path: Path | None = None) -> Settings:
    """Load local settings from disk."""
    target = settings_path(path)
    if not target.exists():
        return Settings()
    payload = tomllib.loads(target.read_text(encoding="utf-8"))
    role = str(payload.get("role", "")).strip() or None
    package_location = str(payload.get("package_location", "")).strip() or None
    raw_roles = payload.get("roles", list(DEFAULT_RUNTIME_ROLES))
    roles = tuple(str(item).strip() for item in raw_roles if str(item).strip()) or DEFAULT_RUNTIME_ROLES
    raw_groups = payload.get("groups", [])
    groups = tuple(
        RoleGroup(
            name=_normalize_name(str(item.get("name", ""))),
            template=str(item.get("template", "custom")).strip() or "custom",
            description=str(item.get("description", "")).strip() or None,
            members=tuple(
                _normalize_name(str(member))
                for member in item.get("members", [])
                if str(member).strip()
            ),
        )
        for item in raw_groups
        if str(item.get("name", "")).strip()
    )
    raw_group_memberships = payload.get("group_memberships", [])
    group_memberships = tuple(
        _normalize_name(str(item))
        for item in raw_group_memberships
        if str(item).strip()
    )
    raw_states = payload.get("states", {})
    states = tuple(
        (str(key).strip(), str(value).strip())
        for key, value in raw_states.items()
        if str(key).strip() and str(value).strip()
    )
    return Settings(
        role=role,
        package_location=package_location,
        roles=roles,
        groups=groups,
        group_memberships=group_memberships,
        states=states,
    )


def save_settings(settings: Settings, path: Path | None = None) -> Path:
    """Persist local settings to disk."""
    target = settings_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# homebase local settings", ""]
    if settings.role:
        lines.append(f'role = "{_escape(settings.role)}"')
    if settings.package_location:
        lines.append(f'package_location = "{_escape(settings.package_location)}"')
    role_values = ", ".join(f'"{_escape(role)}"' for role in settings.roles)
    lines.append(f"roles = [{role_values}]")
    if settings.group_memberships:
        membership_values = ", ".join(f'"{_escape(name)}"' for name in settings.group_memberships)
        lines.append(f"group_memberships = [{membership_values}]")
    if settings.states:
        lines.append("")
        lines.append("[states]")
        for key, value in settings.states:
            lines.append(f'"{_escape(key)}" = "{_escape(value)}"')
    for group in settings.groups:
        lines.append("")
        lines.append("[[groups]]")
        lines.append(f'name = "{_escape(group.name)}"')
        lines.append(f'template = "{_escape(group.template)}"')
        if group.description:
            lines.append(f'description = "{_escape(group.description)}"')
        member_values = ", ".join(f'"{_escape(member)}"' for member in group.members)
        lines.append(f"members = [{member_values}]")
    target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return target


def set_role(role: str | None, path: Path | None = None) -> Settings:
    """Update the internal runtime role used by init and command gating."""
    current = load_settings(path)
    updated = Settings(
        role=role,
        package_location=current.package_location,
        roles=current.roles,
        groups=current.groups,
        group_memberships=current.group_memberships,
        states=current.states,
    )
    save_settings(updated, path)
    return updated


def set_package_location(package_location: str | None, path: Path | None = None) -> Settings:
    """Update the stored package location."""
    current = load_settings(path)
    updated = Settings(
        role=current.role,
        package_location=package_location,
        roles=current.roles,
        groups=current.groups,
        group_memberships=current.group_memberships,
        states=current.states,
    )
    save_settings(updated, path)
    return updated


def add_role(role: str, path: Path | None = None) -> Settings:
    """Add one internal runtime role choice used by init."""
    normalized = _normalize_name(role)
    current = load_settings(path)
    if normalized in current.roles:
        return current
    updated = Settings(
        role=current.role,
        package_location=current.package_location,
        roles=current.roles + (normalized,),
        groups=current.groups,
        group_memberships=current.group_memberships,
        states=current.states,
    )
    save_settings(updated, path)
    return updated


def list_roles(path: Path | None = None) -> tuple[str, ...]:
    """Return selectable internal runtime roles used by init."""
    return load_settings(path).roles


def remove_role(role: str, path: Path | None = None) -> Settings:
    """Remove one internal runtime role choice."""
    normalized = _normalize_name(role)
    current = load_settings(path)
    roles = tuple(item for item in current.roles if item != normalized)
    if not roles:
        raise ValueError("at least one runtime role must remain")
    updated_role = current.role if current.role in roles else None
    updated = Settings(
        role=updated_role,
        package_location=current.package_location,
        roles=roles,
        groups=current.groups,
        group_memberships=current.group_memberships,
        states=current.states,
    )
    save_settings(updated, path)
    return updated


def role_templates() -> tuple[tuple[str, str], ...]:
    """Return built-in role-group templates."""
    return ROLE_TEMPLATES


def list_groups(path: Path | None = None) -> tuple[RoleGroup, ...]:
    """Return defined local role groups."""
    return load_settings(path).groups


def list_group_memberships(path: Path | None = None) -> tuple[str, ...]:
    """Return current-node role-group memberships."""
    return load_settings(path).group_memberships


def list_states(path: Path | None = None) -> tuple[tuple[str, str], ...]:
    """Return current-node state entries."""
    return load_settings(path).states


def add_group(name: str, *, template: str = "custom", description: str | None = None, path: Path | None = None) -> Settings:
    """Add one role group definition."""
    normalized_name = _normalize_name(name)
    normalized_template = _normalize_name(template)
    current = load_settings(path)
    if any(group.name == normalized_name for group in current.groups):
        raise ValueError(f"group already exists: {normalized_name}")
    updated = Settings(
        role=current.role,
        package_location=current.package_location,
        roles=current.roles,
        groups=current.groups + (RoleGroup(name=normalized_name, template=normalized_template, description=description, members=()),),
        group_memberships=current.group_memberships,
        states=current.states,
    )
    save_settings(updated, path)
    return updated


def remove_group(name: str, path: Path | None = None) -> Settings:
    """Remove one role group definition."""
    normalized_name = _normalize_name(name)
    current = load_settings(path)
    if any(normalized_name in group.members for group in current.groups):
        raise ValueError(f"group is still linked from another group: {normalized_name}")
    groups = tuple(group for group in current.groups if group.name != normalized_name)
    if len(groups) == len(current.groups):
        raise ValueError(f"unknown group: {normalized_name}")
    memberships = tuple(item for item in current.group_memberships if item != normalized_name)
    updated = Settings(
        role=current.role,
        package_location=current.package_location,
        roles=current.roles,
        groups=groups,
        group_memberships=memberships,
        states=current.states,
    )
    save_settings(updated, path)
    return updated


def link_group(parent: str, child: str, path: Path | None = None) -> Settings:
    """Add one child group under one parent group."""
    normalized_parent = _normalize_name(parent)
    normalized_child = _normalize_name(child)
    current = load_settings(path)
    if not any(group.name == normalized_parent for group in current.groups):
        raise ValueError(f"unknown group: {normalized_parent}")
    if not any(group.name == normalized_child for group in current.groups):
        raise ValueError(f"unknown group: {normalized_child}")
    groups: list[RoleGroup] = []
    for group in current.groups:
        if group.name != normalized_parent:
            groups.append(group)
            continue
        if normalized_child in group.members:
            groups.append(group)
            continue
        groups.append(
            RoleGroup(
                name=group.name,
                template=group.template,
                description=group.description,
                members=group.members + (normalized_child,),
            )
        )
    updated = Settings(
        role=current.role,
        package_location=current.package_location,
        roles=current.roles,
        groups=tuple(groups),
        group_memberships=current.group_memberships,
        states=current.states,
    )
    save_settings(updated, path)
    return updated


def unlink_group(parent: str, child: str, path: Path | None = None) -> Settings:
    """Remove one child group from one parent group."""
    normalized_parent = _normalize_name(parent)
    normalized_child = _normalize_name(child)
    current = load_settings(path)
    groups: list[RoleGroup] = []
    found_parent = False
    for group in current.groups:
        if group.name != normalized_parent:
            groups.append(group)
            continue
        found_parent = True
        groups.append(
            RoleGroup(
                name=group.name,
                template=group.template,
                description=group.description,
                members=tuple(item for item in group.members if item != normalized_child),
            )
        )
    if not found_parent:
        raise ValueError(f"unknown group: {normalized_parent}")
    updated = Settings(
        role=current.role,
        package_location=current.package_location,
        roles=current.roles,
        groups=tuple(groups),
        group_memberships=current.group_memberships,
        states=current.states,
    )
    save_settings(updated, path)
    return updated


def assign_group(name: str, path: Path | None = None) -> Settings:
    """Assign the current node to one role group."""
    normalized_name = _normalize_name(name)
    current = load_settings(path)
    if not any(group.name == normalized_name for group in current.groups):
        raise ValueError(f"unknown group: {normalized_name}")
    if normalized_name in current.group_memberships:
        return current
    updated = Settings(
        role=current.role,
        package_location=current.package_location,
        roles=current.roles,
        groups=current.groups,
        group_memberships=current.group_memberships + (normalized_name,),
        states=current.states,
    )
    save_settings(updated, path)
    return updated


def unassign_group(name: str, path: Path | None = None) -> Settings:
    """Remove one role-group assignment from the current node."""
    normalized_name = _normalize_name(name)
    current = load_settings(path)
    memberships = tuple(item for item in current.group_memberships if item != normalized_name)
    updated = Settings(
        role=current.role,
        package_location=current.package_location,
        roles=current.roles,
        groups=current.groups,
        group_memberships=memberships,
        states=current.states,
    )
    save_settings(updated, path)
    return updated


def set_state_value(key: str, value: str, path: Path | None = None) -> Settings:
    """Set one current-node state value."""
    normalized_key = key.strip()
    normalized_value = value.strip()
    if not normalized_key:
        raise ValueError("state key cannot be empty")
    if not normalized_value:
        raise ValueError("state value cannot be empty")
    current = load_settings(path)
    states = dict(current.states)
    states[normalized_key] = normalized_value
    updated = Settings(
        role=current.role,
        package_location=current.package_location,
        roles=current.roles,
        groups=current.groups,
        group_memberships=current.group_memberships,
        states=tuple(states.items()),
    )
    save_settings(updated, path)
    return updated


def unset_state_value(key: str, path: Path | None = None) -> Settings:
    """Remove one current-node state value."""
    normalized_key = key.strip()
    current = load_settings(path)
    states = dict(current.states)
    states.pop(normalized_key, None)
    updated = Settings(
        role=current.role,
        package_location=current.package_location,
        roles=current.roles,
        groups=current.groups,
        group_memberships=current.group_memberships,
        states=tuple(states.items()),
    )
    save_settings(updated, path)
    return updated
