#!/usr/bin/env python3
"""Start a built `.mcpb` the way Claude Desktop would, and check it answers.

`mcpb validate` only reads the manifest. This unpacks the bundle, expands the
manifest's own template variables, spawns exactly the command the manifest
declares, and then talks MCP to it — which is the only way to find out that the
interpreter survived pruning, that the launcher can see ``lib``, that the
vendored wheels match the interpreter's ABI, and that the paths in
``mcp_config`` point at files that exist.

    python3 adapters/claude-desktop/try_bundle.py dist/memshelf-0.2.0-uv.mcpb

A bundle can only be started on the platform it targets: run the macOS bundle
on macOS. The checks are the same on either.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

PROTOCOL = "2025-06-18"


class Server:
    """A spawned bundle, spoken to over stdio."""

    def __init__(self, command: list[str], env: dict[str, str]):
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        self.counter = 0

    def call(self, method: str, params: dict | None = None) -> dict:
        self.counter += 1
        request = {"jsonrpc": "2.0", "id": self.counter, "method": method}
        if params is not None:
            request["params"] = params
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        while True:
            line = self.process.stdout.readline()
            if not line:
                raise RuntimeError(f"server exited before answering {method}\n{self._stderr()}")
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                # Anything non-JSON on stdout would corrupt the stream for a
                # real host, so it is worth reporting rather than skipping.
                raise RuntimeError(f"non-JSON on stdout: {line[:200]!r}") from None
            if message.get("id") == self.counter:
                return message

    def notify(self, method: str) -> None:
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.process.stdin.flush()

    def tool(self, name: str, arguments: dict) -> str:
        answer = self.call("tools/call", {"name": name, "arguments": {"params": arguments}})
        content = answer.get("result", {}).get("content", [{}])
        return content[0].get("text", "") if content else ""

    def handshake(self) -> dict:
        answer = self.call(
            "initialize",
            {
                "protocolVersion": PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "try_bundle", "version": "0"},
            },
        )
        self.notify("notifications/initialized")
        return answer.get("result", {})

    def _stderr(self) -> str:
        try:
            return (self.process.stderr.read() or "")[-2000:]
        except Exception:  # noqa: BLE001 — diagnostics must not raise
            return ""

    def close(self) -> None:
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()


def unpack(bundle: Path, destination: Path) -> Path:
    """Extract the bundle and restore the executable bits the zip carries."""
    with zipfile.ZipFile(bundle) as archive:
        for info in archive.infolist():
            path = Path(archive.extract(info, destination))
            mode = info.external_attr >> 16
            if mode & stat.S_IXUSR:
                path.chmod(path.stat().st_mode | 0o111)
    return destination


def spawn_arguments(manifest: dict, root: Path, shelf: str | None) -> tuple[list[str], dict]:
    """Turn the manifest's ``mcp_config`` into a command line and environment.

    ``shelf=None`` deliberately leaves ``${user_config.shelf_path}`` unexpanded:
    that is what a host does when the user leaves the setting empty, and the
    bundle has to cope with the literal template rather than treat it as a path.
    """

    def expand(value: str) -> str:
        value = value.replace("${__dirname}", str(root))
        if shelf is not None:
            value = value.replace("${user_config.shelf_path}", shelf)
        return value

    config = manifest["server"]["mcp_config"]
    command = [expand(config["command"])] + [expand(a) for a in config.get("args", [])]
    environment = dict(os.environ)
    environment.update({k: expand(v) for k, v in config.get("env", {}).items()})
    return command, environment


def check(condition: bool, message: str, failures: list[str]) -> None:
    print(f"  {'ok  ' if condition else 'FAIL'}  {message}")
    if not condition:
        failures.append(message)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--keep", action="store_true", help="keep the unpacked bundle")
    args = parser.parse_args(argv)

    workspace = Path(tempfile.mkdtemp(prefix="mcpb-try-"))
    failures: list[str] = []
    try:
        root = unpack(args.bundle, workspace / "bundle")
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        print(
            f"{args.bundle.name} — {manifest['name']} {manifest['version']} "
            f"({manifest['server']['type']}, manifest {manifest['manifest_version']})"
        )

        entry = root / manifest["server"]["entry_point"]
        check(
            entry.is_file(), f"entry_point exists ({manifest['server']['entry_point']})", failures
        )

        shelf = workspace / "shelf"
        shelf.mkdir()

        # --- configured: the default shelf comes from the host's setting -----
        command, environment = spawn_arguments(manifest, root, str(shelf))
        declared = Path(command[0])
        if declared.is_absolute():
            check(declared.is_file(), f"command exists ({declared.name})", failures)
            check(
                os.access(declared, os.X_OK), f"command is executable ({declared.name})", failures
            )
        elif not shutil.which(command[0]):
            print(f"  skip  {command[0]} is not on PATH — cannot start this bundle here")
            return 0

        server = Server(command, environment)
        try:
            info = server.handshake().get("serverInfo", {})
            check(bool(info.get("name")), f"handshake ({info.get('name', '?')})", failures)

            listed = server.call("tools/list").get("result", {}).get("tools", [])
            names = {tool["name"] for tool in listed}
            check(len(listed) >= 14, f"tools/list returned {len(listed)} tools", failures)

            promised = {tool["name"] for tool in manifest.get("tools", [])}
            check(promised == names, "manifest tool list matches the server's", failures)

            created = json.loads(server.tool("memshelf_init", {"shelf_path": str(shelf)}))
            check(created.get("status") == "ok", "memshelf_init created a shelf", failures)

            # The point of the whole exercise: no shelf_path in the call.
            index = json.loads(server.tool("memshelf_index", {}))
            check(index.get("status") == "ok", "memshelf_index used the configured shelf", failures)
        finally:
            server.close()

        # --- unconfigured: the setting was left empty ------------------------
        command, environment = spawn_arguments(manifest, root, None)
        server = Server(command, environment)
        try:
            server.handshake()
            # Input validation happens in the MCP SDK, before the tool body, so
            # this failure arrives as the SDK's plain-text error rather than the
            # server's own JSON envelope. Either shape is fine; being legible is
            # not — the text has to say which setting is missing.
            answer = server.tool("memshelf_index", {})
            check(bool(answer.strip()), "no shelf configured is answered, not a crash", failures)
            check("MEMSHELF_SHELF_PATH" in answer, "the error names the setting to fix", failures)
            print(f"        └─ {' '.join(answer.split())[:150]}")
        finally:
            server.close()
    finally:
        if args.keep:
            print(f"\nunpacked at {workspace}")
        else:
            shutil.rmtree(workspace, ignore_errors=True)

    print(f"\n{'FAILED: ' + '; '.join(failures) if failures else 'all checks passed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
