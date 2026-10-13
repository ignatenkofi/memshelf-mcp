"""The shelf-repo workflow templates install one pinned memshelf, with a retry (#195).

`adapters/shelf-repo/workflows/` ships the derived-files bot and the PR guard
that every new shelf repository copies. Two promises live in them that nothing
read before this file:

* both install the same release tag (README, «Как поднять пин»: the bot and the
  guard judge a shelf with one code), and the pin was already edited by hand
  twice;
* a single transient network or GitHub failure must not fail the job before
  its first real step, so the install is a bash retry loop, the one a shelf in
  production already carries.

The loop is checked by running it, not by reading it: GitHub runs `run:` under
`bash -e`, and `pip install …; rc=$?` dies on the first failed attempt there
while `pip install … && exit 0` survives it. Both look like a retry loop.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from packaging.version import Version

from memshelf_mcp import __version__

REPO = Path(__file__).resolve().parents[1]
TEMPLATES = REPO / "adapters" / "shelf-repo" / "workflows"
TEMPLATE_NAMES = ("shelf-derived.yml", "shelf-pr-guard.yml")
# A line that runs the install (bare or after `run:`), not one that mentions it.
INSTALL_RX = re.compile(r"^\s*(?:run:\s*)?(?:python3? -m )?pip install\b.*memshelf")
PIN_RX = re.compile(r'"memshelf-mcp @ (git\+https://github\.com/ignatenkofi/memshelf-mcp@([^"]+))"')

needs_bash = pytest.mark.skipif(
    sys.platform.startswith("win") or shutil.which("bash") is None,
    reason="the step runs under bash on the runner",
)


def _install_command(name: str) -> str:
    """Template *name*'s one memshelf install command, from `pip install` on."""
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if INSTALL_RX.search(line)]
    assert len(lines) == 1, f"{name}: {len(lines)} lines install memshelf, expected one: {lines}"
    return lines[0][lines[0].index("pip install") :].strip()


def _install_script(name: str) -> str:
    """The `run:` of the step that holds that command."""
    command = _install_command(name)
    doc = yaml.safe_load((TEMPLATES / name).read_text(encoding="utf-8"))
    runs = [
        step["run"]
        for job in doc["jobs"].values()
        for step in job["steps"]
        if command in step.get("run", "")
    ]
    assert len(runs) == 1, f"{name}: the install command is in {len(runs)} steps"
    return runs[0]


def test_both_templates_pin_the_same_release_tag():
    pins = {}
    for name in TEMPLATE_NAMES:
        match = PIN_RX.search(_install_command(name))
        assert match, f"{name}: the install does not name the memshelf-mcp git URL"
        pins[name] = match.groups()
    assert len({url for url, _ in pins.values()}) == 1, (
        f"the bot and the guard install different code: {pins}"
    )

    (ref,) = {ref for _, ref in pins.values()}
    assert re.fullmatch(r"v\d+\.\d+\.\d+", ref), f"pin {ref!r} is not a vX.Y.Z release tag"
    assert Version(ref[1:]) <= Version(__version__), (
        f"pin {ref} is newer than this package ({__version__}): no such release yet"
    )


def _run_install(name: str, tmp_path: Path, pip_exit_codes: list[int]) -> tuple[int, str, str]:
    """Run template *name*'s install step the way the runner does (`bash -e`).

    A stub pip answers with *pip_exit_codes*, one per call; a stub sleep
    records how long it was asked to wait instead of waiting.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    codes = tmp_path / "pip-codes"
    codes.write_text("".join(f"{c}\n" for c in pip_exit_codes), encoding="utf-8")
    slept = tmp_path / "slept"
    (bin_dir / "pip").write_text(
        "#!/bin/sh\n"
        f'code=$(head -n 1 "{codes}")\n'
        f'tail -n +2 "{codes}" > "{codes}.rest" && mv "{codes}.rest" "{codes}"\n'
        'exit "$code"\n',
        encoding="utf-8",
    )
    (bin_dir / "sleep").write_text(f'#!/bin/sh\necho "$1" >> "{slept}"\n', encoding="utf-8")
    for stub in ("pip", "sleep"):
        (bin_dir / stub).chmod(0o755)
    script = tmp_path / "install.sh"
    script.write_text(_install_script(name), encoding="utf-8")
    proc = subprocess.run(
        ["bash", "-e", str(script)],
        capture_output=True,
        text=True,
        env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
        timeout=60,
    )
    waits = slept.read_text(encoding="utf-8") if slept.exists() else ""
    return proc.returncode, proc.stdout + proc.stderr, waits


@needs_bash
@pytest.mark.parametrize("name", TEMPLATE_NAMES)
def test_install_retries_three_times_then_fails_loudly(name, tmp_path):
    rc, out, waits = _run_install(name, tmp_path, [1, 1, 1])
    assert (rc, out.count("::warning::"), out.count("::error::")) == (1, 3, 1), out
    # A growing pause between attempts, and none after the last one.
    assert waits.split() == ["15", "30"], waits


@needs_bash
@pytest.mark.parametrize("name", TEMPLATE_NAMES)
def test_install_survives_transient_failures(name, tmp_path):
    rc, out, _ = _run_install(name, tmp_path, [1, 1, 0])
    assert (rc, out.count("::warning::"), out.count("::error::")) == (0, 2, 0), out


@needs_bash
@pytest.mark.parametrize("name", TEMPLATE_NAMES)
def test_install_succeeds_at_once_without_noise(name, tmp_path):
    assert _run_install(name, tmp_path, [0]) == (0, "", "")
