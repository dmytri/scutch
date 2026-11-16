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


def _copy_workspace(dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("scutch.toml", "justfile"):
        src = REPO / name
        if src.is_file():
            shutil.copy(src, dest / name)
    for d in ("operations", "providers", "fixtures", "specs"):
        src = REPO / d
        if src.is_dir():
            shutil.copytree(src, dest / d)
    (dest / ".scutch" / "runs").mkdir(parents=True, exist_ok=True)
    (dest / ".scutch" / "artifacts").mkdir(parents=True, exist_ok=True)
    return dest


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    return _copy_workspace(tmp_path / "ws")


@pytest.fixture()
def shim(tmp_path: Path) -> Path:
    """A PATH entry whose `scutch` is the real CLI entry point."""
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
        self.env = {
            **os.environ,
            "PATH": f"{shim}{os.pathsep}{os.environ['PATH']}",
            "PYTHONPATH": str(REPO),
            "COLUMNS": "120",
        }

    def run(self, argv: list[str], stdin: dict | None = None) -> tuple[dict, int, str]:
        proc = subprocess.run(
            argv,
            input=json.dumps(stdin) if stdin is not None else "",
            capture_output=True,
            text=True,
            cwd=self.workspace,
            env=self.env,
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
