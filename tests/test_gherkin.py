"""Execute features as workflows.

A feature is the unit: its scenarios run in file order against one workspace, so
a step can build on what an earlier step did, and the run stops at the first
failure. That is what makes a feature a *procedure* rather than a checklist — and
it is why these tests exercise features, not isolated scenarios.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scutch.gherkin import parse_feature, run_feature

from .gherkin import LIBRARY, Steps

SPECS = Path(__file__).resolve().parents[1] / "specs"


def features():
    return [pytest.param(p, id=p.stem) for p in sorted(SPECS.rglob("*.feature"))]


@pytest.mark.parametrize("path", features())
def test_feature_runs_as_a_workflow(path, cli, workspace):
    scenarios = parse_feature(path)
    assert scenarios, f"{path.name} defines no scenarios"

    steps = Steps(cli)
    run = run_feature(scenarios, steps, LIBRARY)

    assert run.ok, (
        f"{path.name}: stopped at scenario {run.failure.scenario!r}\n"
        f"  {run.failure.error}\n"
        f"  ran: {run.ran}\n"
        f"  skipped: {run.skipped}"
    )
    assert not run.skipped, f"{path.name}: {len(run.skipped)} scenario(s) never ran"
    assert run.ran == [s.name for s in scenarios], "a feature must run every scenario, in order"


def test_a_feature_stops_at_the_first_failing_scenario(workspace, cli, tmp_path):
    """The behaviour that makes a feature a workflow, not a checklist."""
    from scutch.gherkin import Scenario, run_feature

    from .gherkin import LIBRARY, Steps

    class Failing(Steps):
        def then_envelope_status(self, status: str) -> None:
            if status == "needs_approval":
                raise AssertionError("simulated step failure")

    scenarios = [
        Scenario("w", "step one", [("Then", 'the envelope status is "complete"')]),
        Scenario("w", "step two", [("Then", 'the envelope status is "needs_approval"')]),
        Scenario("w", "step three", [("Then", 'the envelope status is "complete"')]),
    ]
    env = Steps(cli).invoke(["scutch", "run", "saleor.customer.list", "--input", "@-", "--format", "json"])

    steps = Failing(cli)
    steps.envelopes = [env]
    run = run_feature(scenarios, steps, LIBRARY)

    assert not run.ok
    assert run.failure.scenario == "step two"
    assert run.ran == ["step one", "step two"]
    # the dependent step must not run on broken state
    assert run.skipped == ["step three"]


def test_keep_going_is_available_for_diagnosis_only(workspace, cli):
    from scutch.gherkin import Scenario, run_feature

    from .gherkin import LIBRARY, Steps

    class Failing(Steps):
        def then_envelope_status(self, status: str) -> None:
            raise AssertionError("always fails")

    scenarios = [
        Scenario("w", "a", [("Then", 'the envelope status is "complete"')]),
        Scenario("w", "b", [("Then", 'the envelope status is "complete"')]),
    ]
    run = run_feature(scenarios, Failing(cli), LIBRARY, keep_going=True)
    assert not run.ok
    assert run.skipped == []
    # keep_going attempts every scenario; `ran` records attempts, `failure`
    # holds the first error
    assert run.ran == ["a", "b"]
