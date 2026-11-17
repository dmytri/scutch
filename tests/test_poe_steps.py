"""Steps are Poe script tasks, so every seam is an object in one process.

This is rule 2, and it is the reason the convention exists: a step that shells
out through `sh` re-joins argv into a string, so "we call this CLI" degrades to
a grep. As a script task the call is a list, and the assertion is exact.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

needs_poe = pytest.mark.skipif(shutil.which("poe") is None, reason="poe not installed")


def test_poe_tasks_are_all_script_tasks():
    """No shell tasks: a shell string cannot be asserted precisely."""
    import tomllib

    cfg = tomllib.loads((REPO / "pyproject.toml").read_text())
    tasks = cfg["tool"]["poe"]["tasks"]
    assert tasks, "no Poe tasks declared"
    for name, task in tasks.items():
        assert "script" in task, f"{name} is not a script task: {task}"
        assert "cmd" not in task, f"{name} uses a shell command"


@needs_poe
def test_steps_are_discoverable():
    out = subprocess.run(["poe", "-h"], cwd=REPO, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    for name in ("orders", "outstanding", "fulfil", "ga-token", "doctor"):
        assert name in out.stdout, f"{name} not listed by poe -h"


def test_exec_seam_is_an_exact_argv_list(monkeypatch):
    """Double subprocess.run and assert the CLI call, not a joined string."""
    import poe_steps

    calls: list[list[str]] = []

    class Done:
        stdout = "ya29.doubled-token\n"
        returncode = 0

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return Done()

    monkeypatch.setattr(subprocess, "run", fake_run)
    poe_steps.ga_token()

    assert calls == [["gcloud", "auth", "application-default", "print-access-token"]]


def test_exec_seam_double_does_not_reach_the_network(monkeypatch):
    """If the double is absent this would be a real gcloud call; prove it isn't."""
    import poe_steps

    called = {}

    def fake_run(argv, **kwargs):
        called["argv"] = list(argv)

        class Done:
            stdout = "tok\n"
            returncode = 0

        return Done()

    monkeypatch.setattr(subprocess, "run", fake_run)
    poe_steps.ga_token()
    assert called["argv"][0] == "gcloud"


def test_a_step_returns_an_envelope_with_a_continuation(capsys):
    import poe_steps

    class Done:
        stdout = "tok\n"
        returncode = 0

    import subprocess as sp

    original = sp.run
    sp.run = lambda *a, **k: Done()
    try:
        poe_steps.ga_token()
    finally:
        sp.run = original

    env = json.loads(capsys.readouterr().out)
    assert env["kind"] == "scutch.envelope/v1"
    assert env["next"]["argv"][:2] == ["scutch", "run"]
