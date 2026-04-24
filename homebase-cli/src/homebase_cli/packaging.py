"""Helpers for GitHub-based homebase install, upgrade, and version inspection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import importlib.metadata
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


DEFAULT_REPO_URL = "https://github.com/sunwbeck/homebase.git"
DEFAULT_SUBDIRECTORY = "homebase-cli"
INSTALL_STATE_PATH = Path.home() / ".local" / "share" / "homebase-cli" / "install-state.json"
LOG_DIR = Path.home() / ".local" / "share" / "homebase-cli" / "logs"
PACKAGE_NAME = "homebase-cli"
GITHUB_API_ROOT = "https://api.github.com"


class PackageOperationError(RuntimeError):
    """Raised when one package operation fails with a saved log file."""

    def __init__(self, message: str, log_path: Path):
        super().__init__(message)
        self.log_path = log_path


class DeferredSelfUpdate(RuntimeError):
    """Raised when a Windows self-update is scheduled in a helper process."""

    def __init__(self, message: str, helper_pid: int, result_path: Path):
        super().__init__(message)
        self.helper_pid = helper_pid
        self.result_path = result_path


@dataclass(frozen=True)
class LoggedResult:
    """One subprocess result with an attached log path."""

    returncode: int
    stdout: str
    stderr: str
    log_path: Path


@dataclass(frozen=True)
class GitHubVersion:
    """One installable GitHub version candidate."""

    version: str
    ref: str
    summary: str
    published_at: str
    prerelease: bool
    source: str
    url: str

    @property
    def label(self) -> str:
        note = self.summary if self.summary else "no description"
        return f"{self.version} | {note}"


@dataclass(frozen=True)
class InstalledPackageStatus:
    """Current locally installed homebase package status."""

    installed_version: str | None
    repo_url: str | None
    requested_ref: str | None
    resolved_ref: str | None
    summary: str | None
    installed_at: str | None


def _is_windows() -> bool:
    """Return whether the current runtime is Windows."""
    return platform.system().lower() == "windows"


def _user_bin_dir() -> Path:
    """Return the directory used for user-visible command shims."""
    return Path.home() / ".local" / "bin"


def _refresh_windows_command_shims(interpreter: str) -> None:
    """Ensure Windows command shims continue to point at the current venv."""
    if not _is_windows():
        return
    script_dir = Path(interpreter).resolve().parent
    targets = {
        "homebase": script_dir / "homebase.exe",
        "hb": script_dir / "hb.exe",
    }
    if not all(target.exists() for target in targets.values()):
        return
    user_bin = _user_bin_dir()
    user_bin.mkdir(parents=True, exist_ok=True)
    for name, target in targets.items():
        shim_path = user_bin / f"{name}.cmd"
        shim_path.write_text(f'@echo off\n"{target}" %*\n', encoding="ascii")


def _same_interpreter_path(left: str, right: str) -> bool:
    """Return whether two interpreter paths resolve to the same executable."""
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def should_defer_windows_self_update(python_bin: str | None = None) -> bool:
    """Return whether the current operation is a Windows self-update."""
    if not _is_windows():
        return False
    interpreter = python_bin if python_bin is not None else sys.executable
    return _same_interpreter_path(interpreter, sys.executable)


def _windows_helper_python_command(target_interpreter: str) -> list[str] | None:
    """Return an external Python command that can run the Windows self-update helper."""
    py_launcher = shutil.which("py")
    if py_launcher is not None:
        return [py_launcher, "-3"]
    for candidate in ("python", "python3"):
        resolved = shutil.which(candidate)
        if resolved is None:
            continue
        if _same_interpreter_path(resolved, target_interpreter):
            continue
        return [resolved]
    return None


def prepare_windows_self_update(
    ref: str,
    *,
    repo_url: str = DEFAULT_REPO_URL,
    python_bin: str | None = None,
    summary: str | None = None,
) -> tuple[list[str], Path]:
    """Prepare one foreground Windows self-update helper command."""
    interpreter = python_bin if python_bin is not None else sys.executable
    helper_command = _windows_helper_python_command(interpreter)
    if helper_command is None:
        raise RuntimeError("could not find an external Python interpreter for Windows self-update")
    helper_dir = Path.home() / ".local" / "share" / "homebase-cli" / "run"
    helper_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    helper_path = helper_dir / f"self-update-foreground-{stamp}.py"
    package_root = Path(__file__).resolve().parent.parent
    helper_source = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {str(package_root)!r})
        from homebase_cli.packaging import PackageOperationError, install_github_ref  # noqa: E402

        def stage(step, total, label):
            print(f"[{{step}}/{{total}}] {{label}}", flush=True)

        print("Updating Windows local installation...", flush=True)
        try:
            _, status = install_github_ref(
                {ref!r},
                repo_url={repo_url!r},
                python_bin={interpreter!r},
                summary={summary!r},
                on_stage=stage,
            )
        except PackageOperationError as exc:
            print("Package install failed.", flush=True)
            print(f"Log: {{exc.log_path}}", flush=True)
            raise SystemExit(1)
        except Exception as exc:
            print(f"Update failed: {{exc}}", flush=True)
            raise SystemExit(1)

        print(f"Installed version: {{status.installed_version or 'unknown'}}", flush=True)
        print(f"Requested ref: {{status.requested_ref}}", flush=True)
        if status.resolved_ref:
            print(f"Resolved commit: {{status.resolved_ref}}", flush=True)
        """
    ).strip() + "\n"
    helper_path.write_text(helper_source, encoding="utf-8")
    return [*helper_command, str(helper_path)], helper_path


def schedule_windows_self_update(
    ref: str,
    *,
    repo_url: str = DEFAULT_REPO_URL,
    python_bin: str | None = None,
    summary: str | None = None,
    wait_for_pid: int | None = None,
) -> tuple[int, Path, Path]:
    """Schedule one Windows self-update in a helper process and return its PID, result path, and log path."""
    interpreter = python_bin if python_bin is not None else sys.executable
    helper_command = _windows_helper_python_command(interpreter)
    if helper_command is None:
        raise RuntimeError("could not find an external Python interpreter for Windows self-update")
    helper_dir = Path.home() / ".local" / "share" / "homebase-cli" / "run"
    helper_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    helper_path = helper_dir / f"self-update-{stamp}.py"
    result_path = helper_dir / f"self-update-{stamp}.json"
    log_path = helper_dir / f"self-update-{stamp}.log"
    package_root = Path(__file__).resolve().parent.parent
    result_path.write_text(
        json.dumps(
            {
                "ok": False,
                "status": "scheduled",
                "ref": ref,
                "repo_url": repo_url,
                "requested_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    helper_source = textwrap.dedent(
        f"""
        import json
        import os
        import sys
        import time
        from pathlib import Path
        from datetime import UTC, datetime

        sys.path.insert(0, {str(package_root)!r})
        from homebase_cli.packaging import install_github_ref  # noqa: E402

        result_path = Path({str(result_path)!r})
        result_path.write_text(json.dumps({{
            "ok": False,
            "status": "running",
            "started_at": datetime.now(UTC).isoformat(),
        }}, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
        wait_for_pid = {wait_for_pid!r}
        if wait_for_pid is not None:
            for _ in range(240):
                try:
                    os.kill(wait_for_pid, 0)
                except OSError:
                    break
                time.sleep(0.25)
        try:
            _, status = install_github_ref(
                {ref!r},
                repo_url={repo_url!r},
                python_bin={interpreter!r},
                summary={summary!r},
            )
            payload = {{
                "ok": True,
                "status": "done",
                "installed_version": status.installed_version,
                "requested_ref": status.requested_ref,
                "resolved_ref": status.resolved_ref,
                "installed_at": status.installed_at,
            }}
        except Exception as exc:
            payload = {{
                "ok": False,
                "status": "failed",
                "error": str(exc),
                "failed_at": datetime.now(UTC).isoformat(),
            }}
        result_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
        """
    ).strip() + "\n"
    helper_path.write_text(helper_source, encoding="utf-8")
    log_handle = log_path.open("a", encoding="utf-8")
    process = subprocess.Popen(
        [*helper_command, str(helper_path)],
        stdout=log_handle,
        stderr=log_handle,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    log_handle.close()
    return process.pid, result_path, log_path


def wait_for_windows_self_update(
    result_path: Path,
    *,
    timeout_seconds: float = 120.0,
    poll_interval: float = 0.25,
    on_tick: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Wait for one scheduled Windows self-update to finish and return the final payload."""
    deadline = time.monotonic() + timeout_seconds
    last_payload: dict[str, Any] = {}
    while time.monotonic() < deadline:
        if on_tick is not None:
            on_tick()
        if result_path.exists():
            try:
                payload = json.loads(result_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = {}
            if isinstance(payload, dict):
                last_payload = payload
                status = str(payload.get("status", "")).strip().lower()
                if status in {"done", "failed"}:
                    return payload
        time.sleep(poll_interval)
    if last_payload:
        return last_payload
    return {"ok": False, "status": "timeout", "error": "timed out waiting for Windows self-update result"}


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


def _cleanup_tempdir(holder: tempfile.TemporaryDirectory[str], *, on_tick: Callable[[], None] | None = None) -> str | None:
    """Best-effort cleanup for one temporary directory, tolerating Windows file-lock delays."""
    last_error: OSError | None = None
    for _ in range(20):
        try:
            holder.cleanup()
            return None
        except OSError as exc:
            last_error = exc
            if on_tick is not None:
                on_tick()
            time.sleep(0.25)
    return str(last_error) if last_error is not None else "temporary directory cleanup failed"


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


def _normalize_repo_url(repo_url: str) -> str:
    """Return the canonical repo URL used for package operations."""
    normalized = repo_url.strip()
    if not normalized:
        raise ValueError("repo URL cannot be empty")
    return normalized


def github_repo_slug(repo_url: str) -> str:
    """Parse one GitHub repo URL into owner/repo form."""
    normalized = _normalize_repo_url(repo_url)
    patterns = (
        r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$",
        r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$",
    )
    for pattern in patterns:
        match = re.match(pattern, normalized)
        if match:
            return f"{match.group('owner')}/{match.group('repo')}"
    raise ValueError(f"unsupported GitHub repo URL: {repo_url}")


def github_archive_url(repo_url: str, ref: str) -> str:
    """Return the GitHub tarball URL for one ref."""
    normalized_ref = ref.strip()
    if not normalized_ref:
        raise ValueError("git ref cannot be empty")
    normalized_repo = _normalize_repo_url(repo_url)
    repo_root = normalized_repo[:-4] if normalized_repo.endswith(".git") else normalized_repo
    return f"{repo_root}/archive/{quote(normalized_ref, safe='')}.tar.gz"


def github_install_target(repo_url: str, ref: str, subdirectory: str = DEFAULT_SUBDIRECTORY) -> str:
    """Return the GitHub tarball URL used to install one ref."""
    _ = subdirectory
    return github_archive_url(repo_url, ref)


def install_command(repo_url: str, ref: str, python_bin: str = "python3") -> str:
    """Render a shell command that installs or updates one GitHub ref."""
    target = github_install_target(repo_url, ref)
    repo_name = github_repo_slug(repo_url).split("/")[-1]
    return (
        f'tmpdir="$(mktemp -d)" && '
        f'curl -fsSL {shlex.quote(target)} | tar -xzf - -C "$tmpdir" && '
        f'{shlex.quote(python_bin)} -m pip install --upgrade --force-reinstall --no-cache-dir '
        f'"$tmpdir/{repo_name}-{ref}/{DEFAULT_SUBDIRECTORY}"'
    )


def _download_archive(url: str, destination: Path) -> None:
    """Download one GitHub archive to disk."""
    request = Request(url, headers={"User-Agent": "homebase-cli"})
    with urlopen(request, timeout=30) as response:
        destination.write_bytes(response.read())


def _prepare_install_source(repo_url: str, ref: str, subdirectory: str = DEFAULT_SUBDIRECTORY) -> tuple[tempfile.TemporaryDirectory[str], Path]:
    """Download and unpack one GitHub ref, returning the installable subdirectory."""
    archive_url = github_archive_url(repo_url, ref)
    tempdir = tempfile.TemporaryDirectory(prefix="homebase-install-")
    root = Path(tempdir.name)
    archive_path = root / "source.tar.gz"
    _download_archive(archive_url, archive_path)
    with tarfile.open(archive_path, "r:gz") as handle:
        handle.extractall(root)
    unpacked_dirs = [item for item in root.iterdir() if item.is_dir()]
    if not unpacked_dirs:
        tempdir.cleanup()
        raise RuntimeError(f"failed to unpack the homebase source tree from {archive_url}")
    install_root = unpacked_dirs[0] / subdirectory
    if not install_root.is_dir():
        tempdir.cleanup()
        raise RuntimeError(f"missing install subdirectory `{subdirectory}` in {archive_url}")
    return tempdir, install_root


def _github_token() -> str | None:
    """Return an auth token from env or gh when available."""
    for key in ("GH_TOKEN", "GITHUB_TOKEN"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    gh_path = shutil.which("gh")
    if gh_path is None:
        return None
    result = subprocess.run(
        [gh_path, "auth", "token"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    token = result.stdout.strip()
    return token or None


def _fetch_json(url: str) -> Any:
    """Fetch one JSON payload from GitHub."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "homebase-cli",
    }
    token = _github_token()
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        url,
        headers=headers,
    )
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 404 and token is None:
            raise RuntimeError(
                "GitHub API request failed with HTTP 404. "
                "This usually means the repo is private and no GitHub token is available. "
                "Run `gh auth login` again or set `GH_TOKEN`/`GITHUB_TOKEN`."
            ) from exc
        raise RuntimeError(f"GitHub API request failed with HTTP {exc.code} for {url}") from exc
    except URLError as exc:
        raise RuntimeError(f"GitHub API request failed for {url}: {exc.reason}") from exc


def _github_api_url(repo_url: str, suffix: str) -> str:
    slug = github_repo_slug(repo_url)
    return f"{GITHUB_API_ROOT}/repos/{slug}{suffix}"


def _summarize_body(body: str | None) -> str:
    """Return one short one-line summary from release notes."""
    if body is None:
        return ""
    for line in body.splitlines():
        normalized = line.strip().lstrip("-*# ").strip()
        if normalized:
            return normalized[:120]
    return ""


def github_versions(
    repo_url: str = DEFAULT_REPO_URL,
    *,
    include_prerelease: bool = False,
    limit: int = 20,
) -> tuple[GitHubVersion, ...]:
    """Return visible versions from GitHub releases, tags, or the default branch."""
    releases_payload = _fetch_json(_github_api_url(repo_url, f"/releases?per_page={max(limit, 1)}"))
    releases: list[GitHubVersion] = []
    for item in releases_payload:
        if item.get("draft"):
            continue
        if item.get("prerelease") and not include_prerelease:
            continue
        releases.append(
            GitHubVersion(
                version=str(item.get("tag_name") or item.get("name") or "unknown"),
                ref=str(item.get("tag_name") or item.get("name") or "unknown"),
                summary=_summarize_body(item.get("body")) or "no release notes",
                published_at=str(item.get("published_at") or ""),
                prerelease=bool(item.get("prerelease")),
                source="release",
                url=str(item.get("html_url") or ""),
            )
        )
    if releases:
        return tuple(releases[:limit])

    tags_payload = _fetch_json(_github_api_url(repo_url, f"/tags?per_page={max(limit, 1)}"))
    tags: list[GitHubVersion] = []
    for item in tags_payload:
        name = str(item.get("name") or "")
        if not name:
            continue
        tags.append(
            GitHubVersion(
                version=name,
                ref=name,
                summary="tag without release notes",
                published_at="",
                prerelease=False,
                source="tag",
                url=f"https://github.com/{github_repo_slug(repo_url)}/tree/{quote(name, safe='')}",
            )
        )
    if tags:
        return tuple(tags[:limit])

    repo_payload = _fetch_json(_github_api_url(repo_url, ""))
    default_branch = str(repo_payload.get("default_branch") or "main")
    return (
        GitHubVersion(
            version=default_branch,
            ref=default_branch,
            summary="default branch",
            published_at="",
            prerelease=False,
            source="branch",
            url=f"https://github.com/{github_repo_slug(repo_url)}/tree/{quote(default_branch, safe='')}",
        ),
    )


def latest_github_version(repo_url: str = DEFAULT_REPO_URL, *, include_prerelease: bool = False) -> GitHubVersion:
    """Return the preferred latest install target from GitHub."""
    versions = github_versions(repo_url, include_prerelease=include_prerelease, limit=20)
    return versions[0]


def resolve_github_ref(repo_url: str, ref: str) -> str:
    """Resolve one GitHub ref to a commit SHA."""
    payload = _fetch_json(_github_api_url(repo_url, f"/commits/{quote(ref, safe='')}"))
    return str(payload.get("sha") or ref)


def installed_version(python_bin: str | None = None) -> str | None:
    """Return the installed homebase version for one interpreter."""
    if python_bin is None:
        try:
            return importlib.metadata.version(PACKAGE_NAME)
        except importlib.metadata.PackageNotFoundError:
            return _installed_version_via_pip_show(sys.executable)
    result = subprocess.run(
        [
            python_bin,
            "-c",
            (
                "import importlib.metadata as m; "
                "import sys; "
                "try: print(m.version('homebase-cli')); "
                "except m.PackageNotFoundError: sys.exit(1)"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return _installed_version_via_pip_show(python_bin)
    return result.stdout.strip() or _installed_version_via_pip_show(python_bin)


def _installed_version_via_pip_show(python_bin: str) -> str | None:
    """Return the installed version by parsing `pip show` output."""
    result = subprocess.run(
        [python_bin, "-m", "pip", "show", PACKAGE_NAME],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("Version:"):
            value = line.split(":", 1)[1].strip()
            return value or None
    return None


def load_install_state(path: Path = INSTALL_STATE_PATH) -> InstalledPackageStatus:
    """Load the stored local install state."""
    version = installed_version()
    if not path.exists():
        return InstalledPackageStatus(
            installed_version=version,
            repo_url=None,
            requested_ref=None,
            resolved_ref=None,
            summary=None,
            installed_at=None,
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    return InstalledPackageStatus(
        installed_version=version,
        repo_url=str(payload.get("repo_url") or "") or None,
        requested_ref=str(payload.get("requested_ref") or "") or None,
        resolved_ref=str(payload.get("resolved_ref") or "") or None,
        summary=str(payload.get("summary") or "") or None,
        installed_at=str(payload.get("installed_at") or "") or None,
    )


def save_install_state(status: InstalledPackageStatus, path: Path = INSTALL_STATE_PATH) -> Path:
    """Persist the local install state."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "installed_version": status.installed_version,
        "repo_url": status.repo_url,
        "requested_ref": status.requested_ref,
        "resolved_ref": status.resolved_ref,
        "summary": status.summary,
        "installed_at": status.installed_at,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def install_github_ref(
    ref: str,
    *,
    repo_url: str = DEFAULT_REPO_URL,
    python_bin: str | None = None,
    summary: str | None = None,
    on_tick: Callable[[], None] | None = None,
    on_stage: Callable[[int, int, str], None] | None = None,
) -> tuple[LoggedResult, InstalledPackageStatus]:
    """Install or update from one GitHub ref and persist local install state."""
    interpreter = python_bin if python_bin is not None else sys.executable
    cleanup_warning: str | None = None
    if on_stage is not None:
        on_stage(1, 6, f"downloading GitHub archive {github_repo_slug(repo_url)}@{ref}")
    try:
        source_dir_holder, install_root = _prepare_install_source(repo_url, ref)
    except RuntimeError as exc:
        log_path = _new_log_path("package-install")
        _write_log(log_path, f"download/install preparation failed\n\n{exc}\n")
        raise PackageOperationError(str(exc), log_path) from exc
    try:
        if on_stage is not None:
            on_stage(2, 6, f"prepared install source in {install_root}")
            on_stage(3, 6, f"running {Path(interpreter).name} -m pip install --upgrade --force-reinstall")
        result = _run_logged(
            [interpreter, "-m", "pip", "install", "--upgrade", "--force-reinstall", "--no-cache-dir", str(install_root)],
            cwd=install_root,
            log_prefix="package-install",
            on_tick=on_tick,
        )
    finally:
        cleanup_warning = _cleanup_tempdir(source_dir_holder, on_tick=on_tick)
    if result.returncode != 0:
        raise PackageOperationError("install failed", result.log_path)
    _refresh_windows_command_shims(interpreter)
    status = InstalledPackageStatus(
        installed_version=installed_version(interpreter),
        repo_url=_normalize_repo_url(repo_url),
        requested_ref=ref,
        resolved_ref=None,
        summary=summary,
        installed_at=None,
    )
    if on_stage is not None:
        on_stage(4, 6, f"verified installed version {status.installed_version or 'unknown'}")
    status = InstalledPackageStatus(
        installed_version=status.installed_version,
        repo_url=status.repo_url,
        requested_ref=status.requested_ref,
        resolved_ref=resolve_github_ref(repo_url, ref),
        summary=status.summary,
        installed_at=datetime.now(UTC).isoformat(),
    )
    if on_stage is not None:
        on_stage(5, 6, f"resolved Git commit {status.resolved_ref or 'unknown'}")
    save_install_state(status)
    if on_stage is not None:
        on_stage(6, 6, f"saved install state to {INSTALL_STATE_PATH}")
    if cleanup_warning:
        log_body = result.log_path.read_text(encoding="utf-8") if result.log_path.exists() else ""
        _write_log(result.log_path, log_body + f"\n\ncleanup warning:\n{cleanup_warning}\n")
    return result, status
