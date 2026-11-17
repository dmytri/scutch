"""`scutch workflow` — a feature executed as a workflow."""

from __future__ import annotations

import json


def _feature(workspace, text: str) -> str:
    path = workspace / "specs" / "saleor" / "smoke.feature"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return "specs/saleor/smoke.feature"


def test_listing_a_feature_does_not_run_it(workspace, cli):
    env, code, err = cli.scutch(
        "workflow", "--list", "specs/saleor/customer_read.feature", "--format", "json"
    )
    assert code == 0, err
    assert env["count"] >= 1
    # a feature has scenarios; each scenario has steps
    assert env["scenarios"]
    assert env["scenarios"][0]["name"]
    assert env["scenarios"][0]["steps"] >= 1


def test_a_feature_runs_as_a_workflow(workspace, cli):
    env, code, err = cli.scutch("workflow", "specs/saleor/customer_read.feature")
    assert code == 0, err
    assert env["ok"] is True
    assert env["ran"], "no scenario ran"
    assert env["skipped"] == []


def test_a_failing_step_stops_the_workflow(workspace, cli):
    path = _feature(
        workspace,
        """Feature: smoke
  Scenario: one
    Given a fresh workspace
    When I list customers
    Then the envelope status is "complete"

  Scenario: two
    When I list customers
    Then the envelope status is "needs_approval"

  Scenario: three
    When I list customers
    Then the envelope status is "complete"
""",
    )
    env, code, _ = cli.scutch("workflow", path)
    assert code == 1
    assert env["ok"] is False
    assert env["failed_at"] == "two"
    # the dependent step must not run on broken state
    assert env["skipped"] == ["three"]


def test_keep_going_attempts_every_step(workspace, cli):
    path = _feature(
        workspace,
        """Feature: smoke
  Scenario: one
    Then the envelope status is "needs_approval"

  Scenario: two
    Then the envelope status is "needs_approval"
""",
    )
    env, code, _ = cli.scutch("workflow", path, "--keep-going")
    assert code == 1
    assert env["skipped"] == []
    assert env["ran"] == ["one", "two"]


def test_an_unknown_feature_is_a_structured_error(workspace, cli):
    env, code, err = cli.scutch("workflow", "does-not-exist")
    assert code != 0
    assert "no feature" in err
