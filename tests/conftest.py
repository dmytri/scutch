"""Shared fixtures: an isolated workspace copy per test, plus a `scutch` shim.

Continuation tests execute the *published* ``next.argv`` through a real
subprocess, so the CLI surface — not an internal helper — is what gets proven.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def _ignore_caches(directory: str, names: list[str]) -> set[str]:
    """Never copy bytecode into a test workspace.

    A stale .pyc shadows an edited source file, so the workspace would run code
    that no longer exists in the repo — a test failure that looks like a bug.
    """
    return {n for n in names if n == "__pycache__" or n.endswith(".pyc")}


def _copy_workspace(dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("scutch.toml", "justfile"):
        src = REPO / name
        if src.is_file():
            shutil.copy(src, dest / name)
    for d in ("operations", "providers", "fixtures", "specs"):
        src = REPO / d
        if src.is_dir():
            shutil.copytree(src, dest / d, ignore=_ignore_caches)
    (dest / ".scutch" / "runs").mkdir(parents=True, exist_ok=True)
    (dest / ".scutch" / "artifacts").mkdir(parents=True, exist_ok=True)
    return dest


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    return _copy_workspace(tmp_path / "ws")


@pytest.fixture()
def shim(tmp_path: Path) -> Path:
    """A PATH entry whose `scutch` is the real CLI entry point.

    Every invocation appends its transport request log to a file, so scenarios
    can assert on what would have crossed the wire. The log is written by the
    child process, which is the only place that knows what it sent.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "scutch"
    exe.write_text(
        "#!/bin/sh\n"
        f'exec "{PYTHON}" -m scutch.cli "$@"\n'
    )
    exe.chmod(0o755)
    return bin_dir


class Cli:
    """Run the CLI in a subprocess, exactly as a caller would."""

    def __init__(self, workspace: Path, shim: Path):
        self.workspace = workspace
        self.shim = shim
        self.recording: str | None = None
        # Snapshot at provisioning, so `reset` restores the workspace as it was
        # handed to this test — not as the repo is now. A test that mutates the
        # workspace (a mutant, a broken operation) must survive a reset.
        self._pristine = workspace.parent / (workspace.name + ".pristine")
        if not self._pristine.exists():
            shutil.copytree(workspace, self._pristine, ignore=_ignore_caches)
        self.env = {
            **os.environ,
            "PATH": f"{shim}{os.pathsep}{os.environ['PATH']}",
            "PYTHONPATH": str(REPO),
            "COLUMNS": "120",
        }

    def reset(self) -> None:
        """Restore this workspace to its provisioned state."""
        for child in list(self.workspace.iterdir()):
            if child.name in ("scutch.toml", "justfile"):
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        shutil.copytree(self._pristine, self.workspace, dirs_exist_ok=True, ignore=_ignore_caches)

    @property
    def wire_file(self) -> Path:
        return self.workspace / ".scutch" / "wire.jsonl"

    def drain_wire(self) -> list[dict]:
        """Transport requests recorded by the CLI processes since last drain."""
        path = self.wire_file
        if not path.is_file():
            return []
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        path.write_text("")
        return rows

    def run(self, argv: list[str], stdin: dict | None = None) -> tuple[dict, int, str]:
        proc = subprocess.run(
            argv,
            input=json.dumps(stdin) if stdin is not None else "",
            capture_output=True,
            text=True,
            cwd=self.workspace,
            env={
                **self.env,
                "SCUTCH_WIRE_LOG": str(self.wire_file),
                **({"SCUTCH_REPLAY": self.recording} if self.recording else {}),
            },
            timeout=120,
        )
        try:
            payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
        except json.JSONDecodeError:
            payload = {"_raw_stdout": proc.stdout}
        return payload, proc.returncode, proc.stderr

    def scutch(self, *args: str, stdin: dict | None = None) -> tuple[dict, int, str]:
        return self.run(["scutch", *args], stdin=stdin)


@pytest.fixture()
def cli(workspace: Path, shim: Path) -> Cli:
    return Cli(workspace, shim)


@pytest.fixture()
def repair_world(cli: Cli) -> Cli:
    """A workspace bound to the recorded repair session (cust_456 gains a grant)."""
    cli.recording = "scenario-repair"
    return cli
