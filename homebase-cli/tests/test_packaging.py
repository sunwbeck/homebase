from pathlib import Path
import json
from urllib.error import HTTPError
from types import SimpleNamespace

from homebase_cli.packaging import (
    DEFAULT_REPO_URL,
    GitHubVersion,
    InstalledPackageStatus,
    github_archive_url,
    github_install_target,
    github_repo_slug,
    github_versions,
    install_command,
    load_install_state,
    save_install_state,
    schedule_windows_self_update,
    should_defer_windows_self_update,
    wait_for_windows_self_update,
    installed_version,
)
from homebase_cli import packaging


def test_install_command_uses_tarball_install_flow() -> None:
    command = install_command(DEFAULT_REPO_URL, "v0.1.0", python_bin="/usr/bin/python3")
    assert "curl -fsSL https://github.com/sunwbeck/homebase/archive/v0.1.0.tar.gz" in command
    assert '/usr/bin/python3 -m pip install --upgrade --force-reinstall --no-cache-dir "$tmpdir/homebase-v0.1.0/homebase-cli"' in command


def test_github_repo_slug_supports_https_and_ssh_urls() -> None:
    assert github_repo_slug("https://github.com/sunwbeck/homebase.git") == "sunwbeck/homebase"
    assert github_repo_slug("git@github.com:sunwbeck/homebase.git") == "sunwbeck/homebase"


def test_github_archive_url_uses_repo_ref() -> None:
    assert github_archive_url(DEFAULT_REPO_URL, "main") == "https://github.com/sunwbeck/homebase/archive/main.tar.gz"


def test_github_install_target_uses_tarball_url() -> None:
    assert github_install_target(DEFAULT_REPO_URL, "main") == "https://github.com/sunwbeck/homebase/archive/main.tar.gz"


def test_github_versions_prefers_releases(monkeypatch) -> None:
    monkeypatch.setattr(
        "homebase_cli.packaging._fetch_json",
        lambda url: [
            {
                "draft": False,
                "prerelease": False,
                "tag_name": "v0.1.1",
                "body": "Pairing fixes\n\nMore details",
                "published_at": "2026-04-23T00:00:00Z",
                "html_url": "https://github.com/sunwbeck/homebase/releases/tag/v0.1.1",
            }
        ],
    )
    versions = github_versions(DEFAULT_REPO_URL)
    assert versions == (
        GitHubVersion(
            version="v0.1.1",
            ref="v0.1.1",
            summary="Pairing fixes",
            published_at="2026-04-23T00:00:00Z",
            prerelease=False,
            source="release",
            url="https://github.com/sunwbeck/homebase/releases/tag/v0.1.1",
        ),
    )


def test_github_versions_falls_back_to_tags(monkeypatch) -> None:
    payloads = [
        [],
        [{"name": "main"}, {"name": "v0.1.0"}],
    ]
    monkeypatch.setattr("homebase_cli.packaging._fetch_json", lambda url: payloads.pop(0))
    versions = github_versions(DEFAULT_REPO_URL)
    assert versions[0].version == "main"
    assert versions[0].source == "tag"
    assert versions[0].summary == "tag without release notes"


def test_github_versions_falls_back_to_default_branch(monkeypatch) -> None:
    payloads = [
        [],
        [],
        {"default_branch": "main"},
    ]
    monkeypatch.setattr("homebase_cli.packaging._fetch_json", lambda url: payloads.pop(0))
    versions = github_versions(DEFAULT_REPO_URL)
    assert versions[0].version == "main"
    assert versions[0].source == "branch"


def test_save_and_load_install_state_round_trip(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "install-state.json"
    monkeypatch.setattr("homebase_cli.packaging.installed_version", lambda python_bin=None: "0.2.0")
    save_install_state(
        InstalledPackageStatus(
            installed_version="0.2.0",
            repo_url=DEFAULT_REPO_URL,
            requested_ref="v0.2.0",
            resolved_ref="abc123",
            summary="release note",
            installed_at="2026-04-23T00:00:00Z",
        ),
        path,
    )
    loaded = load_install_state(path)
    assert loaded.installed_version == "0.2.0"
    assert loaded.requested_ref == "v0.2.0"
    assert loaded.resolved_ref == "abc123"


def test_fetch_json_uses_env_token(monkeypatch) -> None:
    seen = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(request, timeout=20):
        seen["authorization"] = request.headers.get("Authorization")
        return FakeResponse()

    monkeypatch.setenv("GH_TOKEN", "secret-token")
    monkeypatch.setattr("homebase_cli.packaging.urlopen", fake_urlopen)
    from homebase_cli.packaging import _fetch_json

    payload = _fetch_json("https://api.github.com/repos/sunwbeck/homebase/releases")
    assert payload == {"ok": True}
    assert seen["authorization"] == "Bearer secret-token"


def test_fetch_json_404_without_token_has_private_repo_message(monkeypatch) -> None:
    def fake_urlopen(request, timeout=20):
        raise HTTPError(request.full_url, 404, "Not Found", hdrs=None, fp=None)

    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr("homebase_cli.packaging.shutil.which", lambda name: None)
    monkeypatch.setattr("homebase_cli.packaging.urlopen", fake_urlopen)
    from homebase_cli.packaging import _fetch_json

    try:
        _fetch_json("https://api.github.com/repos/sunwbeck/homebase/releases")
    except RuntimeError as exc:
        assert "repo is private" in str(exc)
        assert "gh auth login" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_installed_version_falls_back_to_pip_show_for_python_bin(monkeypatch) -> None:
    calls = []

    def fake_run(args, check=False, capture_output=True, text=True):
        calls.append(args)
        if "-c" in args:
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="Name: homebase-cli\nVersion: 0.1.2\n", stderr="")

    monkeypatch.setattr("homebase_cli.packaging.subprocess.run", fake_run)
    assert installed_version("/tmp/fake-python") == "0.1.2"
    assert calls[1] == ["/tmp/fake-python", "-m", "pip", "show", "homebase-cli"]


def test_refresh_windows_command_shims_creates_cmd_wrappers(tmp_path: Path, monkeypatch) -> None:
    scripts_dir = tmp_path / "venv" / "Scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "python.exe").write_text("", encoding="ascii")
    (scripts_dir / "homebase.exe").write_text("", encoding="ascii")
    (scripts_dir / "hb.exe").write_text("", encoding="ascii")
    monkeypatch.setattr("homebase_cli.packaging.platform.system", lambda: "Windows")
    monkeypatch.setattr("homebase_cli.packaging.Path.home", lambda: tmp_path)

    packaging._refresh_windows_command_shims(str(scripts_dir / "python.exe"))

    homebase_cmd = (tmp_path / ".local" / "bin" / "homebase.cmd").read_text(encoding="ascii")
    hb_cmd = (tmp_path / ".local" / "bin" / "hb.cmd").read_text(encoding="ascii")
    assert '"{}" %*'.format(scripts_dir / "homebase.exe") in homebase_cmd
    assert '"{}" %*'.format(scripts_dir / "hb.exe") in hb_cmd


def test_install_github_ref_refreshes_windows_shims(tmp_path: Path, monkeypatch) -> None:
    install_root = tmp_path / "source" / "homebase-cli"
    install_root.mkdir(parents=True)
    holder = SimpleNamespace(cleanup=lambda: None)
    seen = {}
    monkeypatch.setattr("homebase_cli.packaging.platform.system", lambda: "Windows")
    monkeypatch.setattr("homebase_cli.packaging.Path.home", lambda: tmp_path)
    monkeypatch.setattr("homebase_cli.packaging._prepare_install_source", lambda repo_url, ref: (holder, install_root))
    monkeypatch.setattr(
        "homebase_cli.packaging._run_logged",
        lambda args, cwd, log_prefix, on_tick=None: SimpleNamespace(
            returncode=0,
            stdout="",
            stderr="",
            log_path=tmp_path / "install.log",
        ),
    )
    monkeypatch.setattr("homebase_cli.packaging.installed_version", lambda python_bin=None: "0.2.0")
    monkeypatch.setattr("homebase_cli.packaging.resolve_github_ref", lambda repo_url, ref: "abc123")
    monkeypatch.setattr(
        "homebase_cli.packaging.save_install_state",
        lambda status, path=packaging.INSTALL_STATE_PATH: seen.setdefault("status", status),
    )
    scripts_dir = tmp_path / "venv" / "Scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "python.exe").write_text("", encoding="ascii")
    (scripts_dir / "homebase.exe").write_text("", encoding="ascii")
    (scripts_dir / "hb.exe").write_text("", encoding="ascii")

    packaging.install_github_ref("main", python_bin=str(scripts_dir / "python.exe"))

    assert (tmp_path / ".local" / "bin" / "homebase.cmd").exists()
    assert (tmp_path / ".local" / "bin" / "hb.cmd").exists()
    assert seen["status"].resolved_ref == "abc123"


def test_should_defer_windows_self_update_only_for_current_interpreter(monkeypatch) -> None:
    monkeypatch.setattr("homebase_cli.packaging.platform.system", lambda: "Windows")
    monkeypatch.setattr("homebase_cli.packaging.sys.executable", r"C:\hb\Scripts\python.exe")
    assert should_defer_windows_self_update(r"C:\hb\Scripts\python.exe")
    assert not should_defer_windows_self_update(r"C:\other\python.exe")


def test_schedule_windows_self_update_spawns_helper(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("homebase_cli.packaging.platform.system", lambda: "Windows")
    monkeypatch.setattr("homebase_cli.packaging.Path.home", lambda: tmp_path)
    monkeypatch.setattr("homebase_cli.packaging.sys.executable", str(tmp_path / "venv" / "Scripts" / "python.exe"))
    monkeypatch.setattr("homebase_cli.packaging.shutil.which", lambda name: r"C:\Windows\py.exe" if name == "py" else None)
    captured = {}

    class FakeProcess:
        pid = 4242

    def fake_popen(args, stdout=None, stderr=None, stdin=None, start_new_session=None):
        captured["args"] = args
        return FakeProcess()

    monkeypatch.setattr("homebase_cli.packaging.subprocess.Popen", fake_popen)
    helper_pid, result_path, log_path = schedule_windows_self_update("main")

    assert helper_pid == 4242
    assert result_path.name.endswith(".json")
    assert log_path.name.endswith(".log")
    assert result_path.exists()
    assert captured["args"][:2] == [r"C:\Windows\py.exe", "-3"]
    helper_script = Path(captured["args"][2])
    assert helper_script.exists()
    assert "install_github_ref" in helper_script.read_text(encoding="utf-8")
    result_payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert result_payload["status"] == "scheduled"


def test_wait_for_windows_self_update_returns_done_payload(tmp_path: Path) -> None:
    result_path = tmp_path / "result.json"
    result_path.write_text('{"ok": true, "status": "done", "installed_version": "0.1.45"}\n', encoding="utf-8")
    payload = wait_for_windows_self_update(result_path, timeout_seconds=0.1, poll_interval=0.01)
    assert payload["status"] == "done"
    assert payload["installed_version"] == "0.1.45"


def test_wait_for_windows_self_update_times_out_with_last_payload(tmp_path: Path) -> None:
    result_path = tmp_path / "result.json"
    result_path.write_text('{"ok": false, "status": "running"}\n', encoding="utf-8")
    payload = wait_for_windows_self_update(result_path, timeout_seconds=0.05, poll_interval=0.01)
    assert payload["status"] == "running"


def test_install_github_ref_tolerates_tempdir_cleanup_error(tmp_path: Path, monkeypatch) -> None:
    install_root = tmp_path / "source" / "homebase-cli"
    install_root.mkdir(parents=True)

    class Holder:
        def cleanup(self):
            raise PermissionError("file is in use")

    monkeypatch.setattr("homebase_cli.packaging._prepare_install_source", lambda repo_url, ref: (Holder(), install_root))
    monkeypatch.setattr(
        "homebase_cli.packaging._run_logged",
        lambda args, cwd, log_prefix, on_tick=None: SimpleNamespace(
            returncode=0,
            stdout="ok",
            stderr="",
            log_path=tmp_path / "install.log",
        ),
    )
    monkeypatch.setattr("homebase_cli.packaging._write_log", lambda path, content: path.write_text(content, encoding="utf-8") or path)
    monkeypatch.setattr("homebase_cli.packaging.installed_version", lambda python_bin=None: "0.2.0")
    monkeypatch.setattr("homebase_cli.packaging.resolve_github_ref", lambda repo_url, ref: "abc123")
    monkeypatch.setattr("homebase_cli.packaging.save_install_state", lambda status, path=None: tmp_path / "install-state.json")

    result, status = packaging.install_github_ref("main", python_bin="/tmp/fake-python")

    assert result.returncode == 0
    assert status.resolved_ref == "abc123"
    assert "cleanup warning" in (tmp_path / "install.log").read_text(encoding="utf-8")
