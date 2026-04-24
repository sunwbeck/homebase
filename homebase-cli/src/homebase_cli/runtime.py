"""Helpers for restarting the local homebase daemon after package updates."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import ntpath
import os
import subprocess
import sys
import textwrap

from homebase_cli.paths import LOCAL_CLI_ROOT


def _is_windows() -> bool:
    """Return whether the current runtime is Windows."""
    return os.name == "nt"


def _background_python_executable(interpreter: str) -> str:
    """Return the preferred Python executable for detached helpers."""
    if not _is_windows():
        return interpreter
    normalized = ntpath.normcase(interpreter)
    if normalized.endswith("python.exe"):
        candidate = ntpath.join(ntpath.dirname(interpreter), "pythonw.exe")
        if Path(candidate).exists():
            return candidate
    return interpreter


def _background_popen_kwargs() -> dict[str, object]:
    """Return subprocess kwargs for detached background helpers."""
    if _is_windows():
        flags = (
            int(getattr(subprocess, "DETACHED_PROCESS", 0))
            | int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            | int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        )
        return {
            "creationflags": flags,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
    return {
        "start_new_session": True,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }


def schedule_daemon_restart(
    *,
    python_bin: str | None = None,
    delay_seconds: float = 1.0,
) -> Path:
    """Schedule one detached `homebase daemon restart` helper and return its script path."""
    interpreter = python_bin or sys.executable
    helper_python = _background_python_executable(interpreter)
    helper_dir = LOCAL_CLI_ROOT / "run"
    helper_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    helper_path = helper_dir / f"daemon-restart-{stamp}.py"
    helper_source = textwrap.dedent(
        f"""
        import subprocess
        import sys
        import time

        time.sleep({float(delay_seconds)!r})
        subprocess.run(
            [{interpreter!r}, "-m", "homebase_cli.cli", "daemon", "restart"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        """
    ).strip() + "\n"
    helper_path.write_text(helper_source, encoding="utf-8")
    subprocess.Popen([helper_python, str(helper_path)], **_background_popen_kwargs())
    return helper_path
