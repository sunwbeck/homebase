"""Persistent local settings for one homebase installation."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import tomllib


DEFAULT_SETTINGS_PATH = Path.home() / ".config" / "homebase" / "settings.toml"


@dataclass(frozen=True)
class Settings:
    """Small persistent settings for local homebase behavior."""

    role: str | None = None
    package_location: str | None = None
    roles: tuple[str, ...] = ("control", "client")


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
    raw_roles = payload.get("roles", ["control", "client"])
    roles = tuple(
        str(item).strip()
        for item in raw_roles
        if str(item).strip()
    ) or ("control", "client")
    return Settings(role=role, package_location=package_location, roles=roles)


def save_settings(settings: Settings, path: Path | None = None) -> Path:
    """Persist local settings to disk."""
    target = settings_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# homebase local settings", ""]
    if settings.role:
        lines.append(f'role = "{settings.role}"')
    if settings.package_location:
        escaped = settings.package_location.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'package_location = "{escaped}"')
    role_values = ", ".join(f'"{role}"' for role in settings.roles)
    lines.append(f"roles = [{role_values}]")
    target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return target


def set_role(role: str | None, path: Path | None = None) -> Settings:
    """Update the local node role."""
    current = load_settings(path)
    updated = Settings(role=role, package_location=current.package_location, roles=current.roles)
    save_settings(updated, path)
    return updated


def set_package_location(package_location: str | None, path: Path | None = None) -> Settings:
    """Update the stored package location."""
    current = load_settings(path)
    updated = Settings(role=current.role, package_location=package_location, roles=current.roles)
    save_settings(updated, path)
    return updated


def add_role(role: str, path: Path | None = None) -> Settings:
    """Add one selectable local role."""
    normalized = role.strip().lower()
    if not normalized:
        raise ValueError("role cannot be empty")
    current = load_settings(path)
    if normalized in current.roles:
        return current
    updated = Settings(
        role=current.role,
        package_location=current.package_location,
        roles=current.roles + (normalized,),
    )
    save_settings(updated, path)
    return updated


def list_roles(path: Path | None = None) -> tuple[str, ...]:
    """Return configured selectable roles."""
    return load_settings(path).roles


def remove_role(role: str, path: Path | None = None) -> Settings:
    """Remove one selectable role."""
    normalized = role.strip().lower()
    current = load_settings(path)
    roles = tuple(item for item in current.roles if item != normalized)
    if not roles:
        raise ValueError("at least one role must remain")
    updated_role = current.role if current.role in roles else None
    updated = Settings(role=updated_role, package_location=current.package_location, roles=roles)
    save_settings(updated, path)
    return updated
