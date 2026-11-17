"""Conventions are enforced, not documented.

Each test breaks one convention and asserts `doctor` fails. A convention an
agent cannot be told is a convention it will not follow, so the enforcement is
what makes agent-authored workflows safe — and these tests are what keep the
enforcement honest.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def doctor(cli) -> dict:
    env, _, _ = cli.scutch("doctor", "--format", "json")
    return env


def check(report: dict, name: str) -> dict:
    for c in report["checks"]:
        if c["check"] == name or c["check"].startswith(name):
            return c
    raise AssertionError(f"no check {name!r} in {[c['check'] for c in report['checks']]}")


def test_context_publishes_the_conventions(cli):
    ctx, _, _ = cli.scutch("context", "--format", "json")
    conv = ctx["conventions"]
    # an agent reads these once, from the CLI, not from a document
    assert conv["queries"] == "duckdb"
    assert conv["steps"] == "poe"
    assert conv["secrets"] == "provider"
    assert conv["feature_is_workflow"] is True
    assert conv["features_root"] == "specs"


def test_baseline_passes(cli):
    report = doctor(cli)
    failing = [c["check"] for c in report["checks"] if not c["ok"] and c.get("required", True)]
    assert report["ok"], failing


def test_inline_secret_fails(cli):
    ops = cli.workspace / "operations" / "orders" / "ledger_ops.py"
    ops.write_text(ops.read_text() + '\nAPI_TOKEN = "sk-abcdefghijklmnopqrstuvwx"\n')
    c = check(doctor(cli), "no_inline_secrets")
    assert not c["ok"]
    assert "ledger_ops.py" in json.dumps(c["detail"])


def test_feature_outside_a_domain_folder_fails(cli):
    specs = cli.workspace / "specs"
    shutil.copy(specs / "orders" / "reading.feature", specs / "flat.feature")
    c = check(doctor(cli), "features_are_workflows")
    assert not c["ok"]
    assert "flat.feature" in json.dumps(c["detail"])


def test_declared_query_that_does_not_exist_fails(cli):
    ops = cli.workspace / "operations" / "orders" / "ledger_ops.py"
    text = ops.read_text().replace(
        "examples=[", 'query="operations/orders/missing.sql",\n    examples=[', 1
    )
    ops.write_text(text)
    c = check(doctor(cli), "queries_are_sql")
    assert not c["ok"]


def test_shell_step_fails_the_poe_convention(cli):
    wf = cli.workspace / "workflows"
    wf.mkdir(exist_ok=True)
    (wf / "legacy.yaml").write_text("steps:\n  - task:\n      modelType: command/shell\n")
    c = check(doctor(cli), "steps_are_poe")
    assert not c["ok"]
    assert "legacy.yaml" in json.dumps(c["detail"])


def test_unclaimed_operation_fails(cli):
    """An operation no feature mentions is undocumented behaviour."""
    for feature in (cli.workspace / "specs").rglob("*.feature"):
        feature.write_text(feature.read_text().replace("orders.list", "orders.other"))
    c = check(doctor(cli), "features")
    assert not c["ok"]
    assert "orders.list" in json.dumps(c["detail"])


def test_doctor_requires_duckdb_because_queries_are_sql(workspace, cli, monkeypatch):
    """A workspace declaring SQL queries cannot run without the binary.

    Found from a clean clone: the tests passed locally only because duckdb
    happened to be on PATH. Discovering that at run time is too late.
    """
    import shutil as _shutil
    from scutch import discovery
    from scutch.workspace import load_workspace

    real_which = _shutil.which
    monkeypatch.setattr(
        discovery.shutil, "which", lambda name: None if name == "duckdb" else real_which(name)
    )
    report = discovery.doctor(load_workspace(workspace))
    check_result = check(report, "duckdb_available")
    assert not check_result["ok"]
    assert not report["ok"]
