"""Helpers for building and installing versioned homebase package artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import json
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
from typing import Callable

from homebase_cli.paths import LOCAL_CLI_ROOT
from homebase_cli.settings import load_settings


DEFAULT_DIST_DIR = LOCAL_CLI_ROOT / "dist"
PACKAGE_INDEX_FILENAME = "packages.json"
BOOTSTRAP_DIRNAME = "bootstrap"
BOOTSTRAP_SCRIPT_NAME = "bootstrap-homebase.sh"
SOURCE_BUNDLE_NAME = "homebase-cli-source.tar.gz"
RECOVERY_DIR = Path.home() / ".local" / "share" / "homebase-cli" / "recovery"
LOG_DIR = Path.home() / ".local" / "share" / "homebase-cli" / "logs"
EXCLUDED_BUNDLE_NAMES = {".git", ".venv", "dist", "build", "__pycache__", ".pytest_cache", ".mypy_cache"}


class PackageOperationError(RuntimeError):
    """Raised when one package operation fails with a saved log file."""

    def __init__(self, message: str, log_path: Path):
        super().__init__(message)
        self.log_path = log_path


@dataclass(frozen=True)
class LoggedResult:
    """One subprocess result with an attached log path."""

    returncode: int
    stdout: str
    stderr: str
    log_path: Path


@dataclass(frozen=True)
class PackageRecord:
    """Metadata about one built wheel artifact."""

    filename: str
    version: str
    message: str
    created_at: str

    @property
    def label(self) -> str:
        """Return a concise display label for interactive selection."""
        note = self.message if self.message else "no message"
        return f"{self.version} | {self.filename} | {note}"


def project_version() -> str:
    """Read the current package version from pyproject.toml."""
    pyproject = LOCAL_CLI_ROOT / "pyproject.toml"
    payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return str(payload["project"]["version"])


def _new_log_path(prefix: str) -> Path:
    """Return a timestamped log path for one package operation."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return LOG_DIR / f"{prefix}-{stamp}.log"


def _write_log(path: Path, content: str) -> Path:
    """Write one operation log to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _python_candidates() -> tuple[str, ...]:
    """Return likely Python interpreters to try for package building."""
    candidates: list[str] = []
    for item in (
        sys.executable,
        getattr(sys, "_base_executable", None),
        shutil.which("python3"),
        "/usr/bin/python3",
    ):
        if not item:
            continue
        normalized = str(item)
        if normalized not in candidates:
            candidates.append(normalized)
    return tuple(candidates)


def _supports_build_backend(python_bin: str) -> bool:
    """Return True when the interpreter can import setuptools.build_meta."""
    probe = subprocess.run(
        [python_bin, "-c", "import setuptools.build_meta"],
        check=False,
        capture_output=True,
        text=True,
    )
    return probe.returncode == 0


def _run_logged(
    args: list[str],
    *,
    cwd: Path,
    log_prefix: str,
    on_tick: Callable[[], None] | None = None,
) -> LoggedResult:
    """Run one subprocess and always persist stdout and stderr."""
    log_path = _new_log_path(log_prefix)
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
    )
    while process.poll() is None:
        if on_tick is not None:
            on_tick()
        time.sleep(0.15)
    stdout, stderr = process.communicate()
    log_body = [
        f"command: {' '.join(shlex.quote(arg) for arg in args)}",
        f"cwd: {cwd}",
        f"returncode: {process.returncode}",
        "",
        "stdout:",
        stdout,
        "",
        "stderr:",
        stderr,
    ]
    _write_log(log_path, "\n".join(log_body))
    return LoggedResult(
        returncode=process.returncode or 0,
        stdout=stdout,
        stderr=stderr,
        log_path=log_path,
    )


def resolve_dist_dir(dist_dir: Path | None = None) -> Path:
    """Resolve the directory used for wheel artifacts."""
    if dist_dir is not None:
        return dist_dir
    settings = load_settings()
    if settings.package_location:
        return Path(settings.package_location)
    return DEFAULT_DIST_DIR


def package_index_path(dist_dir: Path | None = None) -> Path:
    """Return the metadata index path for one package directory."""
    return resolve_dist_dir(dist_dir) / PACKAGE_INDEX_FILENAME


def load_package_index(dist_dir: Path | None = None) -> tuple[PackageRecord, ...]:
    """Load the package metadata index for one directory."""
    path = package_index_path(dist_dir)
    if not path.exists():
        return ()
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = [
        PackageRecord(
            filename=str(item["filename"]),
            version=str(item["version"]),
            message=str(item.get("message", "")),
            created_at=str(item["created_at"]),
        )
        for item in payload
    ]
    return tuple(records)


def save_package_index(records: tuple[PackageRecord, ...], dist_dir: Path | None = None) -> Path:
    """Persist the package metadata index."""
    path = package_index_path(dist_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "filename": item.filename,
            "version": item.version,
            "message": item.message,
            "created_at": item.created_at,
        }
        for item in records
    ]
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def remove_package_record(filename: str, dist_dir: Path | None = None) -> Path:
    """Remove one wheel entry from the package index."""
    target = resolve_dist_dir(dist_dir)
    remaining = tuple(item for item in load_package_index(target) if item.filename != filename)
    return save_package_index(remaining, target)


def record_package(wheel_path: Path, message: str = "") -> PackageRecord:
    """Record one built wheel in the package index."""
    target_dir = wheel_path.parent
    record = PackageRecord(
        filename=wheel_path.name,
        version=project_version(),
        message=message.strip(),
        created_at=datetime.now(UTC).isoformat(),
    )
    current = [item for item in load_package_index(target_dir) if item.filename != record.filename]
    current.append(record)
    current.sort(key=lambda item: item.created_at, reverse=True)
    save_package_index(tuple(current), target_dir)
    return record


def duplicate_wheel_path(wheel_path: Path, dist_dir: Path | None = None) -> Path:
    """Return a unique duplicate wheel path in the target directory."""
    target_dir = resolve_dist_dir(dist_dir)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return target_dir / f"{wheel_path.stem}-{stamp}{wheel_path.suffix}"


def built_wheels(dist_dir: Path | None = None) -> tuple[Path, ...]:
    """Return wheels present in one package directory, newest first."""
    target = resolve_dist_dir(dist_dir)
    return tuple(sorted(target.glob("*.whl"), key=lambda item: item.stat().st_mtime, reverse=True))


def installable_packages(dist_dir: Path | None = None) -> tuple[PackageRecord, ...]:
    """Return indexed packages that still exist on disk."""
    target = resolve_dist_dir(dist_dir)
    existing = {path.name for path in built_wheels(target)}
    indexed = [item for item in load_package_index(target) if item.filename in existing]
    if indexed:
        return tuple(indexed)
    fallback: list[PackageRecord] = []
    for wheel_path in built_wheels(target):
        fallback.append(
            PackageRecord(
                filename=wheel_path.name,
                version="unknown",
                message="",
                created_at=datetime.fromtimestamp(wheel_path.stat().st_mtime, UTC).isoformat(),
            )
        )
    return tuple(fallback)


def resolve_wheel(dist_dir: Path | None = None, wheel_name: str | None = None) -> Path:
    """Resolve one installable wheel by name or newest artifact."""
    target = resolve_dist_dir(dist_dir)
    if wheel_name:
        candidate = target / wheel_name
        if candidate.exists():
            return candidate
        candidate = Path(wheel_name)
        if candidate.exists():
            return candidate.resolve()
        raise ValueError(f"wheel not found: {wheel_name}")
    wheels = built_wheels(target)
    if not wheels:
        recovery_wheel = latest_recovery_wheel()
        if recovery_wheel is not None:
            return recovery_wheel
        raise ValueError(f"no wheels found in {target}")
    return wheels[0]


def build_wheel(
    dist_dir: Path | None = None,
    *,
    on_stage: Callable[[str], None] | None = None,
    on_tick: Callable[[], None] | None = None,
) -> Path:
    """Build one wheel into the target directory."""
    target = resolve_dist_dir(dist_dir)
    if on_stage is not None:
        on_stage(f"Preparing output directory {target}")
    target.mkdir(parents=True, exist_ok=True)
    if on_stage is not None:
        on_stage("Cleaning previous build workspace")
    shutil.rmtree(LOCAL_CLI_ROOT / "build", ignore_errors=True)
    before = {path.name for path in target.glob("*.whl")}
    attempts: list[str] = []
    if on_stage is not None:
        on_stage("Selecting Python build backend")
    for python_bin in _python_candidates():
        if not _supports_build_backend(python_bin):
            attempts.append(f"{python_bin}: missing setuptools.build_meta")
            continue
        if on_stage is not None:
            on_stage(f"Running pip wheel with {python_bin}")
        result = _run_logged(
            [
                python_bin,
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "--no-build-isolation",
                "--wheel-dir",
                str(target),
                ".",
            ],
            cwd=LOCAL_CLI_ROOT,
            log_prefix="package-build",
            on_tick=on_tick,
        )
        if result.returncode == 0:
            break
        attempts.append(f"{python_bin}: failed, see {result.log_path}")
    else:
        log_path = _new_log_path("package-build-failure")
        _write_log(log_path, "\n".join(attempts) or "wheel build failed")
        raise PackageOperationError("build failed", log_path)

    after = sorted(target.glob("*.whl"), key=lambda item: item.stat().st_mtime)
    new_paths = [path for path in after if path.name not in before]
    if new_paths:
        wheel_path = new_paths[-1]
    elif after:
        wheel_path = after[-1]
    else:
        raise RuntimeError("wheel build completed but no wheel was found")
    return wheel_path


def build_duplicate_wheel(
    dist_dir: Path | None = None,
    *,
    on_tick: Callable[[], None] | None = None,
) -> Path:
    """Build one wheel and store it with a unique duplicate filename."""
    target = resolve_dist_dir(dist_dir)
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="homebase-wheel-build-") as tmp_dir_name:
        tmp_dir = Path(tmp_dir_name)
        wheel_path = build_wheel(tmp_dir, on_tick=on_tick)
        duplicate_path = duplicate_wheel_path(wheel_path, target)
        shutil.move(str(wheel_path), duplicate_path)
    return duplicate_path


def install_command(wheel_path: Path, python_bin: str = "python3") -> str:
    """Render a shell command that installs or upgrades one built wheel."""
    return f"{shlex.quote(python_bin)} -m pip install --upgrade {shlex.quote(str(wheel_path))}"


def install_wheel(
    wheel_path: Path,
    python_bin: str | None = None,
    *,
    on_tick: Callable[[], None] | None = None,
) -> LoggedResult:
    """Install or upgrade the current Python environment from one wheel."""
    interpreter = python_bin if python_bin is not None else sys.executable
    result = _run_logged(
        [interpreter, "-m", "pip", "install", "--upgrade", str(wheel_path)],
        cwd=LOCAL_CLI_ROOT,
        log_prefix="package-install",
        on_tick=on_tick,
    )
    if result.returncode == 0:
        preserve_recovery_wheel(wheel_path)
    return result


def publish_root(dist_dir: Path | None = None) -> Path:
    """Return the shared publish root derived from the package directory."""
    return resolve_dist_dir(dist_dir).parent


def bootstrap_publish_dir(dist_dir: Path | None = None) -> Path:
    """Return the directory where bootstrap artifacts are published."""
    return publish_root(dist_dir) / BOOTSTRAP_DIRNAME


def source_bundle_path(dist_dir: Path | None = None) -> Path:
    """Return the source bundle path in the publish directory."""
    return bootstrap_publish_dir(dist_dir) / SOURCE_BUNDLE_NAME


def bootstrap_script_path(dist_dir: Path | None = None) -> Path:
    """Return the published bootstrap script path."""
    return bootstrap_publish_dir(dist_dir) / BOOTSTRAP_SCRIPT_NAME


def create_source_bundle(
    dist_dir: Path | None = None,
    *,
    on_tick: Callable[[], None] | None = None,
) -> Path:
    """Create a tar.gz source bundle for bootstrap installation."""
    target = source_bundle_path(dist_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    paths = [LOCAL_CLI_ROOT]
    for path in sorted(LOCAL_CLI_ROOT.rglob("*")):
        relative_parts = path.relative_to(LOCAL_CLI_ROOT).parts
        if any(part in EXCLUDED_BUNDLE_NAMES for part in relative_parts):
            continue
        if path.is_file() and path.suffix in {".pyc", ".pyo"}:
            continue
        paths.append(path)
    with tarfile.open(target, "w:gz") as archive:
        for path in paths:
            archive.add(path, arcname=path.relative_to(LOCAL_CLI_ROOT.parent))
            if on_tick is not None:
                on_tick()
    return target


def publish_bootstrap(
    dist_dir: Path | None = None,
    *,
    on_tick: Callable[[], None] | None = None,
) -> tuple[Path, Path]:
    """Publish the bootstrap script and source bundle into the shared location."""
    bootstrap_dir = bootstrap_publish_dir(dist_dir)
    bootstrap_dir.mkdir(parents=True, exist_ok=True)
    source_script = LOCAL_CLI_ROOT / "scripts" / BOOTSTRAP_SCRIPT_NAME
    target_script = bootstrap_script_path(dist_dir)
    shutil.copy2(source_script, target_script)
    target_script.chmod(0o755)
    if on_tick is not None:
        on_tick()
    bundle = create_source_bundle(dist_dir, on_tick=on_tick)
    return target_script, bundle


def recovery_wheels() -> tuple[Path, ...]:
    """Return locally preserved recovery wheels, newest first."""
    if not RECOVERY_DIR.exists():
        return ()
    return tuple(sorted(RECOVERY_DIR.glob("*.whl"), key=lambda item: item.stat().st_mtime, reverse=True))


def latest_recovery_wheel() -> Path | None:
    """Return the latest locally preserved recovery wheel when available."""
    wheels = recovery_wheels()
    return wheels[0] if wheels else None


def preserve_recovery_wheel(wheel_path: Path) -> Path:
    """Keep a local copy of one installed wheel for recovery use."""
    RECOVERY_DIR.mkdir(parents=True, exist_ok=True)
    target = RECOVERY_DIR / wheel_path.name
    shutil.copy2(wheel_path, target)
    current = RECOVERY_DIR / "current.whl"
    shutil.copy2(wheel_path, current)
    return target


def delete_package(wheel_path: Path, dist_dir: Path | None = None) -> None:
    """Delete one wheel file and remove its metadata entry."""
    wheel_path.unlink(missing_ok=True)
    remove_package_record(wheel_path.name, dist_dir or wheel_path.parent)
