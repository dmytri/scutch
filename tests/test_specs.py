"""Specs are checked, so they cannot quietly drift away from the code."""
import shutil
from pathlib import Path

import pytest

from scutch import specs

REPO = Path(__file__).resolve().parents[1]


def test_every_operation_has_a_spec_that_matches():
    rows = specs.check(REPO)
    assert rows, "no operations found"
    problems = [r for r in rows if r["problems"]]
    assert not problems, problems


def test_a_missing_spec_is_a_failure(tmp_path):
    shutil.copytree(REPO / "operations", tmp_path / "operations")
    (tmp_path / "operations" / "orders" / "list.spec.yaml").unlink()
    rows = specs.check(tmp_path)
    listed = next(r for r in rows if r["operation"] == "orders.list")
    assert listed["problems"]


def test_a_spec_naming_the_wrong_operation_is_a_failure(tmp_path):
    shutil.copytree(REPO / "operations", tmp_path / "operations")
    spec = tmp_path / "operations" / "orders" / "list.spec.yaml"
    spec.write_text(spec.read_text().replace("operation: orders.list", "operation: somewhere.else"))
    rows = specs.check(tmp_path)
    listed = next(r for r in rows if r["operation"] == "orders.list")
    assert any("somewhere.else" in p for p in listed["problems"])


def test_a_spec_documenting_a_parameter_that_does_not_exist_is_a_failure(tmp_path):
    shutil.copytree(REPO / "operations", tmp_path / "operations")
    spec = tmp_path / "operations" / "orders" / "list.spec.yaml"
    text = spec.read_text()
    # Insert under `parameters:` rather than at the end of the file.
    spec.write_text(
        text.replace(
            "parameters:\n",
            "parameters:\n  imaginary:\n    description: nope\n",
        )
    )
    rows = specs.check(tmp_path)
    listed = next(r for r in rows if r["operation"] == "orders.list")
    assert any("do not exist" in p for p in listed["problems"])


def test_a_spec_omitting_a_required_parameter_is_a_failure(tmp_path):
    shutil.copytree(REPO / "operations", tmp_path / "operations")
    spec = tmp_path / "operations" / "orders" / "fulfil.spec.yaml"
    spec.write_text(
        "operation: orders.fulfil\nintent: x\nparameters: {}\neffect: plan_apply\n"
    )
    rows = specs.check(tmp_path)
    listed = next(r for r in rows if r["operation"] == "orders.fulfil")
    assert any("omits required" in p for p in listed["problems"])


def test_a_workflow_spec_must_name_the_steps_the_code_calls(tmp_path):
    shutil.copytree(REPO / "operations", tmp_path / "operations")
    spec = tmp_path / "operations" / "usage" / "analyse.spec.yaml"
    spec.write_text(spec.read_text().replace("  - usage.vercel", "  - usage.something_else"))
    rows = specs.check(tmp_path)
    listed = next(r for r in rows if r["operation"] == "usage.analyse")
    assert any("steps" in p for p in listed["problems"])


def test_the_cli_check_exits_non_zero_on_a_problem(tmp_path):
    import json
    import subprocess
    import sys

    shutil.copytree(REPO / "operations", tmp_path / "operations")
    (tmp_path / "operations" / "orders" / "list.spec.yaml").unlink()
    done = subprocess.run(
        [sys.executable, "-m", "scutch.cli", "check"],
        cwd=tmp_path, capture_output=True, text=True,
        env={"PYTHONPATH": str(REPO), "PATH": "/usr/bin:/bin"},
    )
    assert done.returncode == 1
    assert json.loads(done.stdout)["ok"] is False
