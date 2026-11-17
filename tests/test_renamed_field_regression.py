"""End to end: a renamed field must refuse, not propose a mutation.

This is the concrete scenario that motivated the response type:

  cassette says the customer HAS read_orders
  field is renamed in the response
  -> without the type: plan says "add read_orders" (a mutation, wrongly)
  -> with the type:    status failed, error names the field, nothing planned
"""

from __future__ import annotations

import json
import shutil

import pytest

from scutch.runner import execute
from scutch.workspace import load_workspace

REPO = __import__("pathlib").Path(__file__).resolve().parents[1]


@pytest.fixture()
def workspace_with_renamed_field(tmp_path, monkeypatch):
    for name in ("operations", "providers", "specs", "fixtures"):
        src = REPO / name
        if src.is_dir():
            shutil.copytree(src, tmp_path / name)
    shutil.copy(REPO / "scutch.toml", tmp_path / "scutch.toml")

    cassette = tmp_path / "fixtures" / "saleor" / "scenario-nochange.json"
    data = json.loads(cassette.read_text())
    for exchange in data["exchanges"]:
        user = exchange["response"].get("user")
        if isinstance(user, dict) and "channelPermissions" in user:
            user["permissions"] = user.pop("channelPermissions")
    cassette.write_text(json.dumps(data))

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SCUTCH_REPLAY", "scenario-nochange")
    monkeypatch.setenv("SCUTCH_FIXTURES", str(tmp_path / "fixtures"))
    return load_workspace(tmp_path)


def test_renamed_field_refuses_instead_of_planning_a_mutation(workspace_with_renamed_field):
    env, _ = execute(
        workspace_with_renamed_field,
        "saleor.customer.repair",
        {"customer_id": "cust_123", "permission": "read_orders", "channel": "retail"},
    )
    assert env["status"] == "failed", f"expected refusal, got {env['status']}: {env.get('plan')}"
    assert env.get("plan") is None, "a broken response must not produce a plan"
    error = env["error"]
    assert "channelPermissions" in error["message"]
    assert error["code"] in ("provider_error",)


def test_the_unrenamed_cassette_still_plans_nothing(workspace_with_renamed_field, tmp_path):
    """Control: restore the real field and the same call reports no_change."""
    cassette = tmp_path / "fixtures" / "saleor" / "scenario-nochange.json"
    data = json.loads(cassette.read_text())
    for exchange in data["exchanges"]:
        user = exchange["response"].get("user")
        if isinstance(user, dict) and "permissions" in user:
            user["channelPermissions"] = user.pop("permissions")
    cassette.write_text(json.dumps(data))

    env, _ = execute(
        workspace_with_renamed_field,
        "saleor.customer.repair",
        {"customer_id": "cust_123", "permission": "read_orders", "channel": "retail"},
    )
    assert env["status"] == "complete"
    assert (env.get("result") or {}).get("status") == "no_change"
