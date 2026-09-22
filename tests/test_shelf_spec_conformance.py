"""What memshelf writes conforms to shelf-spec — checked, not assumed.

``memshelf init`` writes ``shelf.yml``, the manifest shelf-spec validates;
``shelve`` and ``rebuild`` write everything else the spec has an opinion on
(episode frontmatter, ``ledger.tsv``, ``INDEX.md``, ``.meta.json``). Until
this test nothing in the repository ran ``shelf-spec validate`` over any of
it, and the manifest carried a key the schema rejected (``policy.patterns``)
from the day ``init`` started writing it — on the project's own ``shelf/``
and on every shelf ``init`` made.

Opt-in by environment variable, not ``pytest.importorskip("shelf_spec")``:
importorskip turns a missing validator into a green run, the failure mode
ci.yml's import assertion exists to prevent. With ``MEMSHELF_CONFORMANCE=1``
(conformance.yml sets it) a missing shelf-spec fails with the install
command in the message; without it the test skips and says how to turn it
on. The skip is a decision, not an accident.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

pytest.importorskip("docshelf_mcp")

from memshelf_mcp.core.init import init_shelf  # noqa: E402
from memshelf_mcp.core.rebuild import rebuild  # noqa: E402
from memshelf_mcp.core.shelve import shelve  # noqa: E402

INSTALL_HINT = "pip install git+https://github.com/ignatenkofi/shelf-spec.git"

DIGEST = (
    "The conformance fixture: init, shelve and rebuild wrote this shelf and "
    "shelf-spec validate judges the result. The decided scaffold is the CLI "
    "path, not a hand-written fixture; a static fixture was rejected because "
    "it would pass while the tools drift. Open: nothing."
)


def _validator() -> list[str]:
    """The `shelf-spec validate --ci --strict` command — or skip / fail as configured."""
    if os.environ.get("MEMSHELF_CONFORMANCE") != "1":
        pytest.skip(
            "set MEMSHELF_CONFORMANCE=1 to run the shelf-spec conformance check "
            f"(needs shelf-spec installed: {INSTALL_HINT})"
        )
    try:
        import shelf_spec  # noqa: F401
    except ImportError:
        pytest.fail(f"MEMSHELF_CONFORMANCE=1 but shelf-spec is not installed — {INSTALL_HINT}")
    return [sys.executable, "-m", "shelf_spec", "validate", "--ci", "--strict"]


def test_a_shelf_written_by_memshelf_passes_shelf_spec_validate(tmp_path):
    validator = _validator()

    # The user's path, not a fixture: init writes the manifest, shelve the
    # episode, rebuild the derived files the spec also reads.
    init_shelf(tmp_path, name="conformance")
    shelve(
        tmp_path,
        slug="2026-09-21-conformance",
        kind="topic",
        digest=DIGEST,
        sections={"Decisions": "Scaffold through the tools, not a fixture."},
        date="2026-09-21",
    )
    rebuild(tmp_path)

    proc = subprocess.run([*validator, str(tmp_path)], capture_output=True, text=True)
    report = json.loads(proc.stdout) if proc.stdout.strip() else {}
    findings = [
        f"{f['rule']} ({f['severity']}) {f['path']}: {f['detail']}"
        for f in report.get("findings", [])
        if f["severity"] != "info"
    ]
    assert proc.returncode == 0, (
        f"shelf-spec validate --ci --strict exited {proc.returncode} "
        f"(verdict {report.get('verdict')!r}):\n  " + "\n  ".join(findings or [proc.stderr.strip()])
    )
