"""The desktop bundle declares the package's own requirements (#198).

`adapters/claude-desktop/build.py` keeps its own requirement lists: it installs
docshelf-mcp with `--no-deps`, so the dependencies docshelf would have pulled
in are listed next to it. Those lists drifted from `pyproject.toml` twice —
docshelf's floor was caught up by hand once (1bdeccc), and #198 found it and
the mcp and pydantic floors behind again — because a floor raise or a
dependabot bump edits `pyproject.toml` only. Nothing read both files; this
test does.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10, the floor; pytest depends on tomli there
    import tomli as tomllib

REPO = Path(__file__).resolve().parents[1]

# Requirements only the bundle carries: with docshelf-mcp installed --no-deps,
# the bundle declares what docshelf itself requires and memshelf does not.
BUNDLE_ONLY = {"pyyaml"}


def _build_module():
    spec = importlib.util.spec_from_file_location(
        "desktop_build_floors", REPO / "adapters" / "claude-desktop" / "build.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # A dataclass under `from __future__ import annotations` looks its module
    # up in sys.modules to resolve the string annotations.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _by_name(lines: list[str]) -> dict[str, Requirement]:
    out: dict[str, Requirement] = {}
    for line in lines:
        req = Requirement(line)
        name = canonicalize_name(req.name)
        assert name not in out, f"{name} is listed twice: {out[name]} and {req}"
        out[name] = req
    return out


def _shape(req: Requirement) -> tuple[object, ...]:
    # SpecifierSet compares as a set, so ">=2,<3" equals "<3,>=2".
    return (req.specifier, frozenset(req.extras), str(req.marker))


def test_bundle_requirements_equal_pyproject_dependencies():
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    declared = _by_name(pyproject["project"]["dependencies"])
    build = _build_module()
    bundled = _by_name(build.DEPENDENCIES + build.DEPENDENCIES_NO_DEPS)

    missing = sorted(set(declared) - set(bundled))
    assert not missing, f"pyproject.toml requires {missing}, the bundle does not install it"
    unexplained = sorted(set(bundled) - set(declared) - BUNDLE_ONLY)
    assert not unexplained, (
        f"the bundle installs {unexplained}, which pyproject.toml does not require "
        "(a requirement only the bundle needs goes into BUNDLE_ONLY, by name)"
    )
    drifted = {
        name: f"bundle {bundled[name]} != pyproject {declared[name]}"
        for name in sorted(declared)
        if _shape(bundled[name]) != _shape(declared[name])
    }
    assert not drifted, drifted
