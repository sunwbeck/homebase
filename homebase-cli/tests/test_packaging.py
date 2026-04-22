from pathlib import Path

from homebase_cli.packaging import (
    DEFAULT_REPO_URL,
    GitHubVersion,
    InstalledPackageStatus,
    github_install_target,
    github_repo_slug,
    github_versions,
    install_command,
    load_install_state,
    save_install_state,
)


def test_install_command_quotes_git_target() -> None:
    command = install_command(DEFAULT_REPO_URL, "v0.1.0", python_bin="/usr/bin/python3")
    assert command == (
        "/usr/bin/python3 -m pip install --upgrade "
        "'git+https://github.com/sunwbeck/homebase.git@v0.1.0#subdirectory=homebase-cli'"
    )


def test_github_repo_slug_supports_https_and_ssh_urls() -> None:
    assert github_repo_slug("https://github.com/sunwbeck/homebase.git") == "sunwbeck/homebase"
    assert github_repo_slug("git@github.com:sunwbeck/homebase.git") == "sunwbeck/homebase"


def test_github_install_target_uses_repo_ref_and_subdirectory() -> None:
    assert github_install_target(DEFAULT_REPO_URL, "main") == (
        "git+https://github.com/sunwbeck/homebase.git@main#subdirectory=homebase-cli"
    )


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
