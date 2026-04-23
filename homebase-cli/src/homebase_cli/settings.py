"""Persistent local settings for one homebase installation."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import tomllib


DEFAULT_SETTINGS_PATH = Path.home() / ".config" / "homebase" / "settings.toml"
RUNTIME_ROLES = ("control", "managed")


@dataclass(frozen=True)
class Settings:
    """Persistent local settings for one homebase installation."""

    role: str | None = None
    package_location: str | None = None


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _normalize_runtime_role(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized == "client":
        return "managed"
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
    return Settings(
        role=_normalize_runtime_role(str(payload.get("role", "")) or None),
        package_location=str(payload.get("package_location", "")).strip() or None,
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
    target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return target


def runtime_roles() -> tuple[str, ...]:
    """Return the allowed runtime roles."""
    return RUNTIME_ROLES


def set_role(role: str | None, path: Path | None = None) -> Settings:
    """Update the runtime role used by the current installation."""
    normalized = _normalize_runtime_role(role)
    if normalized is not None and normalized not in RUNTIME_ROLES:
        raise ValueError(f"role must be one of: {', '.join(RUNTIME_ROLES)}")
    current = load_settings(path)
    updated = Settings(role=normalized, package_location=current.package_location)
    save_settings(updated, path)
    return updated


def set_package_location(package_location: str | None, path: Path | None = None) -> Settings:
    """Update the stored package location."""
    current = load_settings(path)
    updated = Settings(role=current.role, package_location=package_location)
    save_settings(updated, path)
    return updated
