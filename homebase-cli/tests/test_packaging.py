from pathlib import Path
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
    installed_version,
)


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
