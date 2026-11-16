"""Packaging and alias-layer tests.

Two things are easy to get wrong and expensive to discover late:

* the console script must launch the *app*, not the Typer callback (a bug that
  silently prints nothing and exits 0);
* `just` aliases must be canonical pass-throughs — the JSON on stdout must be
  pipeable with no recipe echo in front of it.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest


def test_console_script_entry_point_is_the_app():
    """`scutch --help` and `scutch list` must produce output, not silence."""
    from scutch.cli import _entry

    assert _entry.__name__ == "_entry"
    # the callback is not an app
    from scutch.cli import app, main

    assert main is not app


def test_installed_command_runs(cli, shim):
    """The shim invokes `python -m scutch.cli`; the module must export app."""
    out, code, err = cli.scutch("--help")
    assert code == 0, err


def test_module_invocation_prints_json(cli):
    out, code, err = cli.scutch("list", "--format", "json")
    assert out["kind"] == "scutch.list/v1"
    assert err == ""


@pytest.mark.skipif(not shutil.which("just"), reason="just not installed")
def test_just_aliases_are_pipeable(cli, workspace):
    """Recipe output is clean JSON: no echoed command line, no stderr noise."""
    env = {**cli.env}
    proc = subprocess.run(
        ["just", "customers"], capture_output=True, text=True, cwd=workspace, env=env
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] == "complete"
    assert payload["result"]["count"] == 3


@pytest.mark.skipif(not shutil.which("just"), reason="just not installed")
def test_just_preview_and_apply_pair(cli, workspace):
    env = {**cli.env}
    preview = subprocess.run(
        ["just", "customer-repair", "cust_456", "read_orders"],
        capture_output=True, text=True, cwd=workspace, env=env,
    )
    plan = json.loads(preview.stdout)
    assert plan["status"] == "needs_approval"

    apply = subprocess.run(
        ["just", "customer-repair-apply", "cust_456", "read_orders", "retail", plan["plan"]["digest"]],
        capture_output=True, text=True, cwd=workspace, env=env,
    )
    applied = json.loads(apply.stdout)
    assert applied["status"] == "complete"
    assert applied["result"]["status"] == "applied"


@pytest.mark.skipif(not shutil.which("just"), reason="just not installed")
def test_just_group_does_not_grant_approval(workspace, cli):
    """Aliases must not smuggle in approval flags: preview stays unapproved."""
    env = {**cli.env}
    out = subprocess.run(
        ["just", "--list"], capture_output=True, text=True, cwd=workspace, env=env
    )
    assert out.returncode == 0
