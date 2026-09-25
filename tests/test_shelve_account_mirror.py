"""The account copy of `shelve` has a source of record in git (claude-bus#45).

The skill also lives as an account skill on claude.ai, pushed to every
signed-in host. Nothing in this repository produced that text, so the only
honest statement anyone could make about it was its sha256 — measured once,
in an agent container, with nothing to compare against. `adapters/claude-account/`
is the comparison; this file is what keeps it a comparison rather than a claim.
"""

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

ADAPTERS = Path(__file__).resolve().parent.parent / "adapters"
MIRROR = ADAPTERS / "claude-account" / "skills" / "shelve" / "SKILL.md"
CHECK = ADAPTERS / "claude-code" / "check-shelve-copies.sh"

# First measured 2026-09-14 on the owner's Mac (the 2026-09-10 container run
# reported the same digest). Repinned 2026-09-25 with #157: steps 4 and 6 and
# the doctor pitfall now read `sync.render_pulled`. Until the owner saves that
# text as the account skill, the live-copy comparison below is red on hosts
# that materialise the old one — which is the alarm working, not a flake.
ACCOUNT_SHA256 = "b3a0ad1c49f8900516305661d0cc60016bbf6375cc35e6564eb02713d78fbcc0"
ACCOUNT_BYTES = 15293

# Where the account copy is materialised. Claude Desktop uses the
# `anthropic-skills` plugin bundle, two UUID levels deep; agent containers join
# the same two ids with `_` under `~/.claude/skills/synced/`.
LIVE_GLOBS = (
    "Library/Application Support/Claude/local-agent-mode-sessions/"
    "skills-plugin/*/*/skills/shelve/SKILL.md",
    ".claude/skills/synced/*/shelve/SKILL.md",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _live_copies() -> list[Path]:
    home = Path.home()
    return sorted({p for g in LIVE_GLOBS for p in home.glob(g)})


def test_the_mirror_is_the_measured_account_copy():
    assert MIRROR.exists(), f"the source of record is gone: {MIRROR}"
    assert MIRROR.stat().st_size == ACCOUNT_BYTES
    assert _sha256(MIRROR) == ACCOUNT_SHA256


def test_the_mirror_is_not_installable():
    """A second skill named `shelve` inside a bundle would collide with the
    packaged one, so this directory carries no plugin manifest."""
    account = ADAPTERS / "claude-account"
    assert not list(account.rglob(".claude-plugin"))
    assert not list(account.rglob("plugin.json"))


def test_the_mirror_passes_the_step_7_rule():
    """It mirrors a live copy, so it is also a copy the step-7 checker judges."""
    result = subprocess.run(
        ["bash", str(CHECK), str(MIRROR)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == f"ok: {MIRROR}"


def test_discovery_reaches_the_desktop_bundle(tmp_path: Path):
    """Until 2026-09-14 the search list stopped at `~/.claude`, so a copy two
    directories away was printed as `none` and read as clean. The fixture is
    the bundle layout, not the owner's disk — the test has to fail on a machine
    that has no Desktop at all."""
    bundle = tmp_path / (
        "Library/Application Support/Claude/local-agent-mode-sessions/"
        "skills-plugin/58452a70/c953fc0c/skills/shelve"
    )
    bundle.mkdir(parents=True)
    (bundle / "SKILL.md").write_text(
        "7. Commit the episode:\n\n```bash\ngit add -A && git commit -m 'shelve: <id>'\n```\n",
        encoding="utf-8",
    )
    env = {**os.environ, "HOME": str(tmp_path)}
    env.pop("MEMSHELF_ROOT", None)
    result = subprocess.run(
        ["bash", str(CHECK), "--discover"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=str(tmp_path),
    )
    assert f"found {bundle / 'SKILL.md'}" in result.stderr
    assert f"FAIL: {bundle / 'SKILL.md'}" in result.stdout
    assert result.returncode == 1, result.stdout + result.stderr


def test_the_live_account_copy_still_matches_the_mirror():
    """Where the copy is materialised — the owner's Mac, an agent container —
    the mirror stops being a record of the past and becomes a comparison that
    runs. Where it is not, there is nothing to compare and the run says so
    rather than passing quietly."""
    live = _live_copies()
    if not live:
        pytest.skip("no account copy is materialised on this host")
    drifted = [str(p) for p in live if _sha256(p) != ACCOUNT_SHA256]
    assert not drifted, (
        "the account copy no longer matches adapters/claude-account: "
        + ", ".join(drifted)
        + " — refresh the mirror and the digest in the same commit"
    )
