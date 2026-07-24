import subprocess

import pytest

from memshelf_mcp.core.remote import (
    PRIVATE,
    PUBLIC,
    UNKNOWN,
    configured_remotes,
    remote_visibility,
    to_https_base,
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/o/r.git", "https://github.com/o/r"),
        ("https://github.com/o/r", "https://github.com/o/r"),
        ("https://user:tok@github.com/o/r.git", "https://github.com/o/r"),
        ("git@github.com:o/r.git", "https://github.com/o/r"),
        ("ssh://git@gitlab.com/group/sub/proj.git", "https://gitlab.com/group/sub/proj"),
        ("git@example.org:team/repo", "https://example.org/team/repo"),
    ],
)
def test_to_https_base_normalizes(url, expected):
    assert to_https_base(url) == expected


@pytest.mark.parametrize("url", ["file:///srv/shelf.git", "/srv/shelf", "../sibling", "~/shelf"])
def test_to_https_base_local_is_unreachable(url):
    assert to_https_base(url) is None


def test_local_remote_is_private_without_network():
    # A local/file remote is reported private and never touches status_fn.
    def boom(url, timeout):  # pragma: no cover - must not be called
        raise AssertionError("network probe attempted for a local remote")

    verdict, _ = remote_visibility("/srv/shelf", status_fn=boom)
    assert verdict == PRIVATE


def test_public_when_git_endpoint_answers_200():
    seen = {}

    def fake(url, timeout):
        seen["url"] = url
        return 200

    verdict, detail = remote_visibility("git@github.com:o/r.git", status_fn=fake)
    assert verdict == PUBLIC
    assert "public" in detail
    # provider-agnostic smart-HTTP probe, ssh normalized to https
    assert seen["url"] == "https://github.com/o/r.git/info/refs?service=git-upload-pack"


@pytest.mark.parametrize("status", [401, 404])
def test_private_when_auth_required(status):
    verdict, _ = remote_visibility(
        "https://github.com/o/private.git", status_fn=lambda u, t: status
    )
    assert verdict == PRIVATE


def test_unexpected_status_is_unknown():
    verdict, _ = remote_visibility("https://host/o/r.git", status_fn=lambda u, t: 500)
    assert verdict == UNKNOWN


def test_network_error_is_unknown_not_a_crash():
    def fail(url, timeout):
        raise OSError("dns go boom")

    verdict, detail = remote_visibility("https://host/o/r.git", status_fn=fail)
    assert verdict == UNKNOWN
    assert "could not reach" in detail


def test_configured_remotes_reads_git(tmp_path):
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    assert configured_remotes(tmp_path) == []
    subprocess.run(
        ["git", "-C", str(tmp_path), "remote", "add", "origin", "https://github.com/o/r.git"],
        check=True,
    )
    remotes = configured_remotes(tmp_path)
    # Assert on structure, not the exact URL: `git remote get-url` expands any
    # global insteadOf rewrite (some CI/sandbox proxies rewrite github URLs).
    assert [r.name for r in remotes] == ["origin"]
    assert remotes[0].url.endswith("o/r.git")


def test_configured_remotes_empty_when_not_a_repo(tmp_path):
    assert configured_remotes(tmp_path) == []
