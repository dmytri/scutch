"""Scaffolding makes the compliant shape the easy one, and stays visibly unfinished."""

from __future__ import annotations

import json

import pytest


def test_scaffold_operation_creates_operation_query_and_feature(workspace, cli):
    env, code, err = cli.scutch(
        "new", "operation", "saleor.grist.master.write", "--effect", "plan_apply", "--query"
    )
    assert code == 0, err
    written = env["written"]
    assert "operations/saleor/grist/master/write.py" in written
    assert "operations/saleor/grist/master/write.sql" in written
    assert any(p.startswith("specs/saleor/") and p.endswith(".feature") for p in written)


def test_scaffolded_operation_loads_and_registers(workspace, cli):
    cli.scutch("new", "operation", "saleor.thing.do", "--effect", "read")
    listing, _, _ = cli.scutch("list", "--format", "json")
    assert "saleor.thing.do" in [o["name"] for o in listing["operations"]]


def test_scaffolded_operation_fails_doctor_until_filled_in(workspace, cli):
    cli.scutch("new", "operation", "saleor.thing.do", "--effect", "read")
    report, _, _ = cli.scutch("doctor", "--format", "json")
    failing = {c["check"]: c["detail"] for c in report["checks"] if not c["ok"]}
    assert "no_unfinished_scaffolds" in failing
    assert any("thing" in p for p in failing["no_unfinished_scaffolds"])


def test_scaffold_operation_includes_a_plan_stub_when_consequential(workspace, cli):
    env, _, _ = cli.scutch("new", "operation", "saleor.thing.write", "--effect", "plan_apply")
    source = (workspace / "operations/saleor/thing/write.py").read_text()
    assert "@plan_for(" in source
    assert "Plan(" in source
    assert 'idempotency_key=["id"]' in source


def test_scaffold_rejects_a_bad_name(workspace, cli):
    env, code, err = cli.scutch("new", "operation", "NotValid")
    assert code != 0
    assert "dotted lowercase" in err


def test_scaffold_refuses_to_overwrite(workspace, cli):
    cli.scutch("new", "operation", "saleor.thing.do")
    env, code, _ = cli.scutch("new", "operation", "saleor.thing.do")
    assert code != 0


def test_scaffolded_query_file_is_runnable_sql(workspace, cli):
    cli.scutch("new", "operation", "saleor.q.run", "--query")
    sql = (workspace / "operations/saleor/q/run.sql").read_text()
    assert sql.strip().startswith("--")
    assert "SELECT" in sql
