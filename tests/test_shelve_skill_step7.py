"""Step 7 of the packaged shelve skill stages by path — and the checker that
proves it can fail (claude-bus#21).

Four copies of the skill drifted for sixteen days on `git add -A` because
nothing compared them. The checker (`adapters/claude-code/check-shelve-copies.sh`)
is the comparison you can run over any set of copies; this file pins the
packaged copy and shows the checker going red on the defect it exists for.
It shells out to bash + awk, like the hook tests — no docshelf / mcp needed.
"""

import re
import subprocess
from pathlib import Path

ADAPTER = Path(__file__).resolve().parent.parent / "adapters" / "claude-code"
SKILL = ADAPTER / "skills" / "shelve" / "SKILL.md"
CHECK = ADAPTER / "check-shelve-copies.sh"

BLANKET_LINE = re.compile(r"^\s*git add (-A|--all|-u|--update|\.)\s*($|&&|;)", re.MULTILINE)
PATH_STAGE = re.compile(r"git add (-- )?\S*docs/")


def _check(*paths: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(CHECK), *(str(p) for p in paths)],
        capture_output=True,
        text=True,
        check=False,
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
