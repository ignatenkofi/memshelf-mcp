#!/usr/bin/env python3
"""Pack memshelf as a Claude Desktop extension (`.mcpb`).

Two bundles, because the two ways of getting Python onto a desktop trade off
against each other:

``uv``
    ~85 KB. ``server.type: "uv"`` — the host resolves Python and the
    dependencies itself. Needs a Claude Desktop that understands manifest 0.4;
    the type is still marked experimental upstream.

``standalone``
    ~23 MB. ``server.type: "python"`` with a python-build-standalone
    interpreter *and* every dependency inside the bundle. Understood by any
    Desktop that reads manifest 0.3, and it does not care whether the machine
    has a Python at all — which on macOS it usually does not, at least not one
    new enough (the system ships 3.9, memshelf needs 3.10+).

Build both and hand over both: install the uv one, fall back to standalone if
the Desktop rejects it.

    python3 adapters/claude-desktop/build.py --out dist

Network is required (PyPI, and the interpreter tarball for ``standalone``).
Downloads are cached under ``--cache`` so a rebuild is offline-ish and quick.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
SRC = REPO / "src"

# Third-party runtime requirements. Deliberately not read from pyproject.toml:
# `docshelf-mcp` is installed WITHOUT its dependency tree here (see below), so
# the two lists cannot be one list.
DEPENDENCIES = ["mcp>=2.0.0,<3", "pydantic>=2.6,<3", "pyyaml>=6.0,<7"]

# docshelf drags in pymupdf4llm -> pymupdf, onnxruntime, numpy, sympy: ~200 MB
# of PDF ingestion that a memory shelf never reaches. memshelf touches exactly
# one docshelf module (`core.shelf`), which imports none of it, so the package
# is installed with --no-deps and the three shared deps above cover it.
DEPENDENCIES_NO_DEPS = ["docshelf-mcp>=0.2,<1"]

# The same amputation for the uv bundle, where resolution happens on the user's
# machine: an override with an unsatisfiable marker removes the requirement
# wherever it is declared.
UV_OVERRIDES = ["pymupdf4llm; sys_platform == 'unreachable'"]

BUNDLE_NAME = "memshelf"


@dataclass(frozen=True)
class Target:
    """One platform the standalone bundle can be built for."""

    key: str
    mcpb_platform: str  # darwin | win32 | linux
    interpreter: str  # path inside the bundle, POSIX form
    runtime_url: str
    runtime_sha256: str
    pip_platforms: tuple[str, ...]
    python_version: str = "3.12"
    abi: tuple[str, ...] = ("cp312", "abi3", "none")


TARGETS = {
    "macos-arm64": Target(
        key="macos-arm64",
        mcpb_platform="darwin",
        interpreter="runtime/bin/python3.12",
        runtime_url=(
            "https://github.com/astral-sh/python-build-standalone/releases/download/"
            "20250902/cpython-3.12.11%2B20250902-aarch64-apple-darwin-install_only_stripped.tar.gz"
        ),
        runtime_sha256="17aa38f6a06eefbaa7757d7f8aa9d7941f169aa9571127b8d346141d2aa532d1",
        pip_platforms=(
            "macosx_11_0_arm64",
            "macosx_11_0_universal2",
            "macosx_10_9_universal2",
        ),
    ),
    # Not a Claude Desktop platform anyone ships on — it is here so the
    # standalone path can be *run* on the build machine. A bundle whose
    # interpreter was over-pruned, whose launcher cannot find `lib`, or whose
    # manifest points at the wrong path fails identically on both targets, and
    # only this one can be started to find out.
    "linux-x86_64": Target(
        key="linux-x86_64",
        mcpb_platform="linux",
        interpreter="runtime/bin/python3.12",
        runtime_url=(
            "https://github.com/astral-sh/python-build-standalone/releases/download/"
            "20250902/cpython-3.12.11%2B20250902-x86_64-unknown-linux-gnu-install_only_stripped"
            ".tar.gz"
        ),
        runtime_sha256="49b3c31d7c51bfb469d6ff77db1b800cbdd7aaa11fafb9af0b517d9aa129b48b",
        pip_platforms=("manylinux2014_x86_64", "manylinux_2_17_x86_64", "manylinux_2_28_x86_64"),
    ),
}

# Trimmed from the interpreter: none of it is reachable from an MCP server over
# stdio, and together it is more than a third of the tarball.
RUNTIME_PRUNE_DIRS = (
    "include",
    "share",
    "lib/pkgconfig",
    "lib/python3.12/test",
    "lib/python3.12/idlelib",
    "lib/python3.12/tkinter",
    "lib/python3.12/turtledemo",
    "lib/python3.12/ensurepip",
)
RUNTIME_PRUNE_GLOBS = (
    "lib/libtcl*",
    "lib/libtk*",
    "lib/tcl*",
    "lib/tk*",
    "lib/itcl*",
    "lib/thread*",
    "lib/sqlite3*",
    "lib/python3.12/lib-dynload/_tkinter*",
    "lib/python3.12/config-3.12-*",  # the static libpython and its Makefile
    "bin/2to3*",
    "bin/idle3*",
    "bin/pydoc3*",
    "bin/pip*",
    "bin/*-config",
)


# --------------------------------------------------------------------------- #
# manifest
# --------------------------------------------------------------------------- #


def package_version() -> str:
    text = (SRC / "memshelf_mcp" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("cannot read __version__ from src/memshelf_mcp/__init__.py")
    return match.group(1)


def tool_roster() -> list[dict[str, str]]:
    """Read the tools out of ``server.py`` without importing it.

    Parsing beats importing here: the build machine does not need the MCP SDK
    installed, and the manifest cannot drift from the server it ships — a tool
    added or renamed shows up in the next bundle by itself.
    """
    import ast

    module = ast.parse((SRC / "memshelf_mcp" / "server.py").read_text(encoding="utf-8"))
    tools: list[dict[str, str]] = []
    for node in module.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if getattr(decorator.func, "attr", None) != "tool":
                continue
            name, title = node.name, ""
            for keyword in decorator.keywords:
                if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                    name = keyword.value.value
                if keyword.arg == "annotations" and isinstance(keyword.value, ast.Dict):
                    for key, value in zip(keyword.value.keys, keyword.value.values, strict=False):
                        if (
                            isinstance(key, ast.Constant)
                            and key.value == "title"
                            and isinstance(value, ast.Constant)
                        ):
                            title = value.value
            if not title:
                title = (ast.get_docstring(node) or "").split("\n")[0]
            tools.append({"name": name, "description": title})
    if len(tools) < 14:
        raise SystemExit(f"only {len(tools)} tools found in server.py — parser out of date?")
    return tools


DESCRIPTION = (
    "Working memory for AI agents: shelve a closed topic as a digest-indexed "
    "episode on a git shelf, recall it a section at a time."
)

LONG_DESCRIPTION = """\
memshelf turns a finished conversation topic into an **episode** on a git-backed
shelf: a Markdown file with a validated digest (<=120 words, named referents, no
secrets), redacted before it is written, plus a row in the shelf's ledger.

What that buys you: the topic leaves the context window but stays addressable.
The shelf's `INDEX.md` is small enough to read whole, and a recall fetches one
episode — or one section of one episode — instead of replaying the conversation.

Recalled text arrives wrapped as data, never as instructions.

Set **Default shelf** below to the folder of a shelf you have already created
(`memshelf_init` will make one). Any call that names its own `shelf_path`
overrides it, so a project can keep a shelf of its own.
"""


def build_manifest(*, variant: str, version: str, target: Target | None) -> dict:
    if variant == "uv":
        server = {
            "type": "uv",
            "entry_point": "src/server.py",
            # The schema requires mcp_config even for uv, where the host is
            # supposed to drive execution. Spelling out the equivalent command
            # keeps the bundle runnable by a host that takes it literally.
            "mcp_config": {
                "command": "uv",
                "args": [
                    "run",
                    "--quiet",
                    "--project",
                    "${__dirname}",
                    "${__dirname}/src/server.py",
                ],
                "env": {
                    "MEMSHELF_SHELF_PATH": "${user_config.shelf_path}",
                    "PYTHONUNBUFFERED": "1",
                },
            },
        }
        manifest_version, platforms = "0.4", ["darwin", "win32", "linux"]
    else:
        assert target is not None
        server = {
            "type": "python",
            "entry_point": "server/main.py",
            "mcp_config": {
                "command": "${__dirname}/" + target.interpreter,
                "args": ["${__dirname}/server/main.py"],
                "env": {
                    "PYTHONPATH": "${__dirname}/server/lib",
                    "PYTHONUNBUFFERED": "1",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "MEMSHELF_SHELF_PATH": "${user_config.shelf_path}",
                },
            },
        }
        manifest_version, platforms = "0.3", [target.mcpb_platform]

    manifest = {
        "manifest_version": manifest_version,
        "name": BUNDLE_NAME,
        "display_name": "memshelf",
        "version": version,
        "description": DESCRIPTION,
        "long_description": LONG_DESCRIPTION,
        "author": {
            "name": "Filipp Ignatenko",
            "email": "ignatenkofi@gmail.com",
            "url": "https://github.com/ignatenkofi",
        },
        "repository": {"type": "git", "url": "https://github.com/ignatenkofi/memshelf-mcp"},
        "homepage": "https://github.com/ignatenkofi/memshelf-mcp",
        "documentation": "https://github.com/ignatenkofi/memshelf-mcp#readme",
        "support": "https://github.com/ignatenkofi/memshelf-mcp/issues",
        "license": "MIT",
        "keywords": ["memory", "context", "shelf", "git", "markdown", "agent"],
        "server": server,
        "tools": tool_roster(),
        "tools_generated": False,
        "user_config": {
            "shelf_path": {
                "type": "directory",
                "title": "Default shelf",
                "description": (
                    "Folder of the memory shelf to use when a call does not name one. "
                    "Leave empty to require an explicit path in every call."
                ),
                "required": False,
            }
        },
        # No `runtimes` entry: the standalone bundle carries its own interpreter
        # and the uv bundle lets the host provide one, so neither depends on a
        # Python being installed.
        "compatibility": {"platforms": platforms},
    }
    return manifest


# --------------------------------------------------------------------------- #
# staging
# --------------------------------------------------------------------------- #


def copy_package(destination: Path) -> None:
    """Copy `memshelf_mcp` itself, minus caches."""
    shutil.copytree(
        SRC / "memshelf_mcp",
        destination / "memshelf_mcp",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


def pip_into(target_dir: Path, target: Target) -> None:
    """Install the third-party dependencies for *target*'s platform and ABI."""
    flags = [
        "--only-binary=:all:",
        "--python-version",
        target.python_version,
        "--implementation",
        "cp",
    ]
    for platform in target.pip_platforms:
        flags += ["--platform", platform]
    for abi in target.abi:
        flags += ["--abi", abi]

    base = [sys.executable, "-m", "pip", "install", "--quiet", "--target", str(target_dir)]
    subprocess.run(base + flags + DEPENDENCIES, check=True)
    subprocess.run(base + flags + ["--no-deps"] + DEPENDENCIES_NO_DEPS, check=True)

    for junk in ("bin", "__pycache__"):
        shutil.rmtree(target_dir / junk, ignore_errors=True)


def fetch_runtime(target: Target, cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / f"{target.key}-python-{target.python_version}.tar.gz"
    if not archive.exists():
        print(f"  downloading interpreter for {target.key} …")
        with urllib.request.urlopen(target.runtime_url) as response:  # noqa: S310 — pinned URL
            archive.write_bytes(response.read())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if target.runtime_sha256 and digest != target.runtime_sha256:
        raise SystemExit(
            f"interpreter checksum mismatch for {target.key}:\n"
            f"  expected {target.runtime_sha256}\n  got      {digest}"
        )
    if not target.runtime_sha256:
        print(f"  interpreter sha256 = {digest}  (record it in TARGETS)")
    return archive


def unpack_runtime(archive: Path, destination: Path) -> None:
    """Extract the interpreter, dereferencing symlinks and dropping the fat.

    Symlinks are resolved into real files on purpose: a `.mcpb` is a zip, and
    whether a given host's unpacker restores links is not something to bet the
    server's entry point on.
    """
    staging = destination.parent / "_runtime-raw"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    with tarfile.open(archive) as tar:
        tar.extractall(staging)  # noqa: S202 — first-party, checksum-pinned archive
    root = staging / "python"

    for relative in RUNTIME_PRUNE_DIRS:
        shutil.rmtree(root / relative, ignore_errors=True)
    for pattern in RUNTIME_PRUNE_GLOBS:
        for path in root.glob(pattern):
            shutil.rmtree(path, ignore_errors=True) if path.is_dir() else path.unlink()

    # copytree with symlinks=False copies link *targets*, which is what we want.
    shutil.copytree(root, destination, symlinks=False)
    shutil.rmtree(staging, ignore_errors=True)


def stage_uv(stage: Path, version: str) -> None:
    stage.mkdir(parents=True)
    copy_package(stage / "src")
    shutil.copy2(HERE / "entrypoint.py", stage / "src" / "server.py")
    overrides = ",\n".join(f'    "{item}"' for item in UV_OVERRIDES)
    dependencies = ",\n".join(f'    "{item}"' for item in DEPENDENCIES + DEPENDENCIES_NO_DEPS)
    (stage / "pyproject.toml").write_text(
        f"""\
# Generated by adapters/claude-desktop/build.py — edit the builder, not this.
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "memshelf-mcp"
version = "{version}"
description = "{DESCRIPTION}"
requires-python = ">=3.10"
dependencies = [
{dependencies},
]

[tool.hatch.build.targets.wheel]
packages = ["src/memshelf_mcp"]

# docshelf declares pymupdf4llm, which pulls ~200 MB of PDF machinery that a
# memory shelf never calls. An override with an unsatisfiable marker drops the
# requirement wherever it is declared.
[tool.uv]
override-dependencies = [
{overrides},
]
""",
        encoding="utf-8",
    )


def stage_standalone(stage: Path, target: Target, cache: Path) -> None:
    stage.mkdir(parents=True)
    server = stage / "server"
    server.mkdir()
    shutil.copy2(HERE / "launcher.py", server / "main.py")
    print("  installing dependencies …")
    pip_into(server / "lib", target)
    copy_package(server / "lib")
    print("  unpacking interpreter …")
    unpack_runtime(fetch_runtime(target, cache), stage / "runtime")


# --------------------------------------------------------------------------- #
# packing
# --------------------------------------------------------------------------- #


def pack(stage: Path, output: Path) -> Path:
    """Zip *stage* into a `.mcpb`, keeping the executable bits.

    Entries are sorted and timestamps fixed so two builds of the same input
    produce byte-identical bundles.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in stage.rglob("*") if p.is_file())
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            info = zipfile.ZipInfo(str(path.relative_to(stage).as_posix()), (1980, 1, 1, 0, 0, 0))
            mode = path.stat().st_mode
            executable = bool(mode & stat.S_IXUSR)
            info.external_attr = (0o755 if executable else 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    return output


def validate(manifest_path: Path) -> None:
    """Run `mcpb validate` when the CLI is around; skip quietly when it is not."""
    mcpb = shutil.which("mcpb")
    if not mcpb:
        print("  (mcpb CLI not installed — skipping schema validation)")
        return
    result = subprocess.run([mcpb, "validate", str(manifest_path)], capture_output=True, text=True)
    print(f"  mcpb validate: {(result.stdout + result.stderr).strip().splitlines()[-1]}")
    if result.returncode != 0:
        raise SystemExit("manifest failed validation")


def human(size: int) -> str:
    return f"{size / 1_048_576:.1f} MB" if size >= 1_048_576 else f"{size / 1024:.0f} KB"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", default=str(REPO / "dist"), help="where to write the .mcpb files")
    parser.add_argument(
        "--variant",
        choices=["uv", "standalone", "both"],
        default="both",
        help="which bundle(s) to build (default: both)",
    )
    parser.add_argument(
        "--target",
        choices=sorted(TARGETS),
        default="macos-arm64",
        help="platform for the standalone bundle",
    )
    parser.add_argument("--cache", default=str(REPO / ".build-cache"))
    parser.add_argument("--keep-stage", action="store_true", help="leave the staging tree behind")
    args = parser.parse_args(argv)

    out = Path(args.out).resolve()
    cache = Path(args.cache).resolve()
    version = package_version()
    target = TARGETS[args.target]
    variants = ["uv", "standalone"] if args.variant == "both" else [args.variant]
    built: list[Path] = []

    for variant in variants:
        stage = out / f"_stage-{variant}"
        shutil.rmtree(stage, ignore_errors=True)
        name = f"{BUNDLE_NAME}-{version}-{'uv' if variant == 'uv' else target.key}.mcpb"
        print(f"building {name}")

        if variant == "uv":
            stage_uv(stage, version)
        else:
            stage_standalone(stage, target, cache)

        manifest = build_manifest(
            variant=variant, version=version, target=None if variant == "uv" else target
        )
        (stage / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        validate(stage / "manifest.json")

        bundle = pack(stage, out / name)
        built.append(bundle)
        print(f"  {bundle}  ({human(bundle.stat().st_size)})")
        if not args.keep_stage:
            shutil.rmtree(stage, ignore_errors=True)

    print("\ndone:")
    for bundle in built:
        print(f"  {bundle}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
