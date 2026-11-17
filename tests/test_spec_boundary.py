"""Pin the boundary of what the specs prove.

`docs/workspace.md` claims specs assert the *workflow* against a recorded
environment, not the API's behaviour. That claim is only honest while these
tests hold. Each one demonstrated a real gap at some point; they are here so the
documentation cannot quietly become marketing.
"""

from __future__ import annotations

import json
import shutil
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


def _scenario(name_fragment: str):
    from tests.gherkin import parse_feature

    for scenario in parse_feature(REPO / "specs" / "api.customer_repair.feature"):
        if name_fragment in scenario.name:
            return scenario
    raise AssertionError(f"no scenario matching {name_fragment!r}")


def test_specs_catch_a_dropped_mutation(workspace, tmp_path):
    """The strongest claim: an apply that does nothing is caught by a scenario."""
    from tests.gherkin import Steps, execute

    ops = workspace / "operations" / "saleor" / "customer_repair.py"
    ops.write_text(
        ops.read_text().replace(
            "    api.apply_permission(customer_id, channel, permission)", "    pass"
        )
    )
    cli = _harness(workspace, tmp_path)
    cli.recording = "scenario-repair"
    with pytest.raises(AssertionError):
        execute(_scenario("documented variables"), Steps(cli))


def test_falsified_recorded_data_is_not_caught(tmp_path):
    """Documents the gap: replay checks requests, not the truth of responses."""
    from tests.conftest import _copy_workspace

    ws = _copy_workspace(tmp_path / "ws")
    cassette = ws / "fixtures" / "saleor" / "scenario-repair.json"
    data = json.loads(cassette.read_text())
    for exchange in data["exchanges"]:
        user = (exchange.get("response") or {}).get("user")
        if user:
            user["email"] = "falsified@example.com"
    cassette.write_text(json.dumps(data, indent=2))

    # The scenario's assertions are about status, changes and the wire — none of
    # them depend on the email — so a false recording passes. That is the point:
    # specs describe our workflow, not the API's data.
    from tests.gherkin import Steps, execute, parse_feature
    from tests.conftest import Cli

    shim = tmp_path / "bin"
    shim.mkdir(exist_ok=True)
    exe = shim / "scutch"
    import sys

    exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m scutch.cli "$@"\n')
    exe.chmod(0o755)
    cli = Cli(ws, shim)
    scenario = [
        s for s in parse_feature(REPO / "specs" / "api.customer_repair.feature")
        if "documented variables" in s.name
    ][0]
    execute(scenario, Steps(cli))  # must not raise


def test_recorded_response_error_is_surfaced(workspace, tmp_path):
    """A recording that says the API refused must fail the workflow loudly."""
    from tests.conftest import Cli

    cassette = workspace / "fixtures" / "saleor" / "scenario-repair.json"
    data = json.loads(cassette.read_text())
    for exchange in data["exchanges"]:
        if "mutation" in exchange["request"]["document"]:
            exchange["response"] = {
                "channelUpdatePermission": {
                    "user": None,
                    "errors": [{"field": "permission", "message": "NOPE", "code": "INVALID"}],
                }
            }
    cassette.write_text(json.dumps(data, indent=2))

    import sys

    shim = tmp_path / "bin"
    shim.mkdir(exist_ok=True)
    exe = shim / "scutch"
    exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m scutch.cli "$@"\n')
    exe.chmod(0o755)
    cli = Cli(workspace, shim)
    cli.recording = "scenario-repair"

    preview, _, _ = cli.scutch(
        "run", "saleor.customer.repair", "--input", "@-", "--format", "json",
        stdin={"customer_id": "cust_456", "permission": "read_orders"},
    )
    applied, code, _ = cli.scutch(
        "run", "saleor.customer.repair", "--input", "@-", "--format", "json",
        "--idempotency-key", "k", "--approve-plan", preview["plan"]["digest"],
        stdin={"customer_id": "cust_456", "permission": "read_orders"},
    )
    assert applied["status"] == "failed"
    assert applied["error"]["code"] == "provider_error"
    assert code == 7


def test_auth_header_is_not_asserted(workspace, tmp_path):
    """Documents the gap: dropping auth changes no scenario.

    Replay never sends a request, so the transport's headers cannot be observed.
    This is why a green suite is not evidence the API accepts our calls.
    """
    provider = workspace / "providers" / "saleor.py"
    text = provider.read_text()
    assert '"Authorization": f"Bearer {self._secret}"' in text
    stripped = text.replace(
        '                "Authorization": f"Bearer {self._secret}",\n', ""
    )
    provider.write_text(stripped)

    from tests.conftest import Cli
    from tests.gherkin import Steps, execute, parse_feature
    import sys

    shim = tmp_path / "bin"
    shim.mkdir(exist_ok=True)
    exe = shim / "scutch"
    exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m scutch.cli "$@"\n')
    exe.chmod(0o755)
    cli = Cli(workspace, shim)
    scenario = [
        s for s in parse_feature(REPO / "specs" / "api.customer_repair.feature")
        if "no credential appears" in " ".join(t for _, t in s.steps)
    ][0]
    execute(scenario, Steps(cli))  # still passes: headers are invisible to replay
