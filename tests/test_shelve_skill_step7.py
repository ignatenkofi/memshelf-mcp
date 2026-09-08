"""Step 7 of the packaged shelve skill stages by path — and the checker that
proves it can fail (claude-bus#21).

Four copies of the skill drifted for sixteen days on `git add -A` because
nothing compared them. The checker (`adapters/claude-code/check-shelve-copies.sh`)
is the comparison you can run over any set of copies; this file pins the
packaged copy and shows the checker going red on the defect it exists for.
It shells out to bash + awk, like the hook tests — no docshelf / mcp needed.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

ADAPTER = Path(__file__).resolve().parent.parent / "adapters" / "claude-code"
SKILL = ADAPTER / "skills" / "shelve" / "SKILL.md"
CHECK = ADAPTER / "check-shelve-copies.sh"

BLANKET_LINE = re.compile(r"^\s*git add (-A|--all|-u|--update|\.)\s*($|&&|;)", re.MULTILINE)
PATH_STAGE = re.compile(r"git add (-- )?\S*docs/")


def _check(
    *args: Path | str,
    script: Path | None = None,
    home: Path | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the checker. `home`/`cwd` are what `--discover` searches, so every
    discovery test sets them: otherwise the verdict would depend on whatever
    copies the developer happens to have synced."""
    env = None
    if home is not None:
        env = {**os.environ, "HOME": str(home)}
        env.pop("MEMSHELF_ROOT", None)
    return subprocess.run(
        ["bash", str(script or CHECK), *(str(a) for a in args)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=None if cwd is None else str(cwd),
    )


def test_packaged_copy_stages_the_episode_by_path():
    text = SKILL.read_text(encoding="utf-8")
    assert not BLANKET_LINE.search(text), "step 7 prescribes a blanket stage"
    assert PATH_STAGE.search(text), "step 7 does not stage the episode by path"

    result = _check(SKILL)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == f"ok: {SKILL}"


def test_a_blanket_stage_in_a_code_block_is_red(tmp_path: Path):
    """The defect the issue was opened on: the old copy's step 7, verbatim."""
    copy = tmp_path / "SKILL.md"
    copy.write_text(
        "7. Commit the episode:\n\n```bash\ngit add -A && git commit -m 'shelve: <id>'\n```\n",
        encoding="utf-8",
    )
    result = _check(copy)
    assert result.returncode == 1, result.stdout
    assert f"FAIL: {copy}" in result.stdout
    assert "4: blanket stage in a code block — git add -A && git commit" in result.stdout
    assert "not staged by path" in result.stdout


def test_a_blanket_stage_prescribed_in_prose_is_red(tmp_path: Path):
    copy = tmp_path / "SKILL.md"
    copy.write_text(
        "7. Stage by path: `git add -- docs/<category>/<id>.md`.\n"
        "   Then run `git add -u` and commit.\n",
        encoding="utf-8",
    )
    result = _check(copy)
    assert result.returncode == 1, result.stdout
    assert "blanket stage prescribed — Then run `git add -u` and commit" in result.stdout


def test_naming_the_command_to_forbid_it_is_not_a_violation(tmp_path: Path):
    """The trap in the issue's first acceptance: repaired copies contain the
    string because they ban it. A negated mention must stay green."""
    copy = tmp_path / "SKILL.md"
    copy.write_text(
        "7. **Stage the episode alone, by path.** Never `git add -A`, never\n"
        "   `git add -u`:\n\n"
        "   ```bash\n   git add -- docs/<category>/<id>.md\n   git commit -m 'shelve: <id>'\n   ```\n",
        encoding="utf-8",
    )
    result = _check(copy)
    assert result.returncode == 0, result.stdout


def test_a_copy_that_never_stages_by_path_is_red(tmp_path: Path):
    copy = tmp_path / "SKILL.md"
    copy.write_text("7. Commit with message `shelve: <id>` and push.\n", encoding="utf-8")
    result = _check(copy)
    assert result.returncode == 1, result.stdout
    assert "not staged by path" in result.stdout


def test_a_missing_copy_is_red_and_the_verdict_covers_every_path(tmp_path: Path):
    result = _check(SKILL, tmp_path / "gone" / "SKILL.md")
    assert result.returncode == 1, result.stdout
    assert f"ok: {SKILL}" in result.stdout
    assert "no such file" in result.stdout


def test_no_paths_is_a_usage_error():
    result = _check()
    assert result.returncode == 2
    assert "usage" in result.stderr


# --- `--discover`: the search list is code, and an empty search says so ------
#
# The acceptance in claude-bus#21 asks for a comparison "you can run with a
# command". The path form is not that command: it needs the operator to know
# where the copies are, which is exactly the knowledge the issue was missing —
# it could not locate the fourth copy at all.


def test_discover_finds_the_packaged_copy_without_being_told_where_it_is(tmp_path: Path):
    result = _check("--discover", home=tmp_path, cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"ok: {SKILL}" in result.stdout
    assert "looked in:" in result.stderr


def test_discover_finds_a_copy_nobody_named(tmp_path: Path):
    """The copy the issue could not reach lives under `~/.claude/skills/synced/
    <id>/` — materialised in agent containers, absent from the machine where
    the search was done. Discovery has to reach it without an argument."""
    synced = tmp_path / ".claude" / "skills" / "synced" / "abc_def" / "shelve"
    synced.mkdir(parents=True)
    (synced / "SKILL.md").write_text(
        "7. Commit the episode:\n\n```bash\ngit add -A && git commit -m 'shelve: <id>'\n```\n",
        encoding="utf-8",
    )
    result = _check("--discover", home=tmp_path, cwd=tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"FAIL: {synced / 'SKILL.md'}" in result.stdout
    assert f"found {synced / 'SKILL.md'}" in result.stderr


def test_an_empty_search_is_reported_as_empty_not_as_clean(tmp_path: Path):
    """rc 2, not 0: a host that exposes no copy has proved nothing. The
    distinction is the same one `demand-select` draws between "no demand" and
    "we could not ask"."""
    stray = tmp_path / "stray"
    stray.mkdir()
    shutil.copy(CHECK, stray / CHECK.name)
    result = _check("--discover", script=stray / CHECK.name, home=tmp_path, cwd=stray)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "nothing to compare" in result.stderr
    assert "not a clean verdict" in result.stderr
    assert result.stdout == ""


def test_a_copy_reached_twice_is_judged_once(tmp_path: Path):
    """Discovery plus an explicit path is the normal call on a shelf checkout;
    a doubled verdict line would be read as two copies agreeing."""
    shelf = tmp_path / ".claude" / "skills" / "shelve"
    shelf.mkdir(parents=True)
    copy = shelf / "SKILL.md"
    copy.write_text(
        "7. **Stage by path.** Never `git add -A`:\n\n"
        "   ```bash\n   git add -- docs/<category>/<id>.md\n   ```\n",
        encoding="utf-8",
    )
    result = _check("--discover", copy, home=tmp_path, cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count(str(copy)) == 1


def test_an_unknown_option_does_not_become_a_path(tmp_path: Path):
    result = _check("--all", home=tmp_path, cwd=tmp_path)
    assert result.returncode == 2
    assert "unknown option" in result.stderr


def test_a_location_with_a_space_is_not_torn_in_half(tmp_path: Path):
    """`$HOME` with a space is ordinary on macOS, and the search list is a
    newline-separated string: default word splitting would turn one location
    into two nonexistent ones and report `none` for a copy that is there."""
    home = tmp_path / "home with space"
    shelf = home / ".claude" / "skills" / "shelve"
    shelf.mkdir(parents=True)
    (shelf / "SKILL.md").write_text(
        "7. **Stage by path.** Never `git add -A`:\n\n"
        "   ```bash\n   git add -- docs/<category>/<id>.md\n   ```\n",
        encoding="utf-8",
    )
    result = _check("--discover", home=home, cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"ok: {shelf / 'SKILL.md'}" in result.stdout
