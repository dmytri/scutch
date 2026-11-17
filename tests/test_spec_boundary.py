"""Pin the boundary of what the specs prove.

`docs/workspace.md` claims specs assert the *workflow* against a recorded
environment, not the API's behaviour. That claim is only honest while these
tests hold. Each one demonstrated a real gap at some point; they are here so the
documentation cannot quietly become marketing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _harness(workspace, tmp_path):
    """A Cli bound to a workspace copy, with a scutch shim on PATH."""
    import sys

    from tests.conftest import Cli

    shim = tmp_path / "bin"
    shim.mkdir(exist_ok=True)
    exe = shim / "scutch"
    exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m scutch.cli "$@"\n')
    exe.chmod(0o755)
    return Cli(workspace, shim)


def _scenario(feature: str, predicate):
    from tests.gherkin import parse_feature

    for scenario in parse_feature(REPO / "specs" / "orders" / feature):
        if predicate(scenario):
            return scenario
    raise AssertionError(f"no matching scenario in {feature}")


def test_specs_catch_a_dropped_mutation(workspace, tmp_path):
    """The strongest claim: an apply that does nothing is caught by a scenario."""
    from tests.gherkin import LIBRARY, Steps, execute

    ops = workspace / "operations" / "orders" / "ledger_ops.py"
    ops.write_text(
        ops.read_text().replace(
            '        ledger.set_status(order_id, "fulfilled")', "        pass"
        )
    )
    cli = _harness(workspace, tmp_path)
    scenario = _scenario("bulk.feature", lambda s: "fulfils them all at once" in s.name)
    with pytest.raises(AssertionError):
        execute(scenario, Steps(cli), LIBRARY)


def test_falsified_seed_data_is_not_caught(tmp_path):
    """Documents the gap: specs check our workflow, not the truth of the store."""
    from tests.conftest import _copy_workspace

    ws = _copy_workspace(tmp_path / "ws")
    seed = ws / "fixtures" / "orders.json"
    data = json.loads(seed.read_text())
    # Falsify a field no scenario asserts on: the customer name, not the status.
    for order in data["orders"]:
        order["customer"] = "falsified@example.com"
    seed.write_text(json.dumps(data, indent=2))

    # The scenario asserts the envelope status and that no approval is required;
    # none of that depends on who placed the order, so a false seed passes. That
    # is the point: specs describe our workflow, not the store's data.
    from tests.gherkin import LIBRARY, Steps, execute

    cli = _harness(ws, tmp_path)
    scenario = _scenario(
        "approval.feature", lambda s: "already-fulfilled order" in s.name
    )
    execute(scenario, Steps(cli), LIBRARY)  # must not raise


def test_provider_failure_is_surfaced(workspace, tmp_path):
    """A store that cannot answer must fail the workflow loudly."""
    from tests.gherkin import LIBRARY, Steps, execute

    seed = workspace / "fixtures" / "orders.json"
    seed.write_text("{ this is not json")

    cli = _harness(workspace, tmp_path)
    scenario = _scenario("errors.feature", lambda s: "does not exist" in s.name)
    # The scenario expects not_found; a corrupt ledger instead yields a
    # provider_error, and the scenario fails rather than passing silently.
    with pytest.raises(AssertionError):
        execute(scenario, Steps(cli), LIBRARY)

    applied, code, _ = cli.scutch(
        "run", "orders.inspect", "--input", "@-", "--format", "json",
        stdin={"order_id": "ord_a1"},
    )
    assert applied["status"] == "failed"
    assert applied["error"]["code"] == "provider_error"
    assert code == 7


# Deleted: `test_auth_header_is_not_asserted` (dropping auth changes no scenario).
# The ledger provider is a local JSON file with no transport and no headers, so
# the replay-invisible-headers gap it documented has no analogue here.
