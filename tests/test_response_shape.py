"""The response shape is a type, not a habit.

A cassette is a memory of bytes. These tests cover the schema that memory is
checked against, and the distinction it exists to preserve:

    "the API renamed this field"     -> refuse, name the field
    "this customer has no permissions" -> legitimate, plan a mutation

Reading responses with ``.get(field, default)`` collapses those two into one
observation, which is how a renamed field once produced a *live mutation
proposal* instead of an error.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scutch.responses import Customer, FieldError, Permission

REPO = Path(__file__).resolve().parents[1]

GOOD = {
    "id": "cust_123",
    "email": "grace@example.com",
    "channelPermissions": [{"channel": {"slug": "retail"}, "permissions": ["read_orders"]}],
}


def test_a_well_formed_response_parses():
    customer = Customer.parse(GOOD)
    assert customer.customer_id == "cust_123"
    assert customer.permissions_on("retail") == ["read_orders"]
    assert customer.permissions_on("wholesale") == []


def test_an_absent_permission_is_a_real_answer_not_an_error():
    """The legitimate case: present key, empty list."""
    customer = Customer.parse({**GOOD, "channelPermissions": []})
    assert customer.permissions_on("retail") == []


def test_a_renamed_field_is_refused_by_name():
    renamed = {k: v for k, v in GOOD.items() if k != "channelPermissions"}
    renamed["permissions"] = GOOD["channelPermissions"]
    with pytest.raises(FieldError) as exc:
        Customer.parse(renamed, where="customer[cust_123]")
    message = str(exc.value)
    assert "channelPermissions" in message
    assert "customer[cust_123]" in message


def test_the_error_names_the_keys_it_did_receive():
    with pytest.raises(FieldError) as exc:
        Customer.parse({"id": "c", "email": None, "granted": []})
    assert exc.value.details["keys"] == ["email", "granted", "id"]


def test_a_malformed_permission_entry_is_refused():
    broken = {**GOOD, "channelPermissions": [{"permissions": ["read_orders"]}]}
    with pytest.raises(FieldError) as exc:
        Customer.parse(broken)
    assert "channel.slug" in str(exc.value)


def test_a_missing_id_is_refused():
    with pytest.raises(FieldError):
        Customer.parse({"email": None, "channelPermissions": []})


def test_round_trip_is_stable():
    assert Customer.parse(GOOD).to_json() == GOOD


# -- through the operation, where the consequence lives --------------------


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    for name in ("operations", "providers", "specs", "fixtures"):
        src = REPO / name
        if src.is_dir():
            shutil.copytree(src, tmp_path / name)
    shutil.copy(REPO / "scutch.toml", tmp_path / "scutch.toml")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SCUTCH_FIXTURES", str(tmp_path / "fixtures"))
    from scutch.workspace import load_workspace

    return load_workspace(tmp_path)


def rename_field(cassette: Path) -> None:
    data = json.loads(cassette.read_text())
    for exchange in data["exchanges"]:
        user = exchange.get("response", {}).get("user")
        if isinstance(user, dict) and "channelPermissions" in user:
            user["permissions"] = user.pop("channelPermissions")
    cassette.write_text(json.dumps(data))


def test_a_rename_fails_instead_of_proposing_a_mutation(workspace, monkeypatch):
    """The regression this type exists to prevent.

    Before the type, a renamed field read as "no permissions", so the operation
    asked for approval to grant a permission the customer already had.
    """
    from scutch.runner import execute

    rename_field(workspace.root / "fixtures" / "saleor" / "scenario-nochange.json")
    monkeypatch.setenv("SCUTCH_REPLAY", "scenario-nochange")

    env, _ = execute(
        workspace,
        "saleor.customer.repair",
        {"customer_id": "cust_123", "permission": "read_orders", "channel": "retail"},
    )
    assert env["status"] == "failed", "a broken response must not become a plan"
    assert env.get("plan") in (None, {"changes": []})
    assert env["error"]["type"] == "FieldError"
    assert "channelPermissions" in env["error"]["message"]


def test_the_real_cassette_still_reports_no_change(workspace, monkeypatch):
    from scutch.runner import execute

    monkeypatch.setenv("SCUTCH_REPLAY", "scenario-nochange")
    env, _ = execute(
        workspace,
        "saleor.customer.repair",
        {"customer_id": "cust_123", "permission": "read_orders", "channel": "retail"},
    )
    assert env["status"] == "complete"
    assert env["result"]["status"] == "no_change"


def test_listing_parses_every_row_through_the_type(workspace, monkeypatch):
    from scutch.runner import execute

    monkeypatch.setenv("SCUTCH_REPLAY", "scenario-list")
    env, _ = execute(workspace, "saleor.customer.list", {"limit": 20})
    assert env["status"] == "complete"
    for row in env["result"]["customers"]:
        assert set(row) >= {"customer_id", "channel_permissions"}


def test_permission_is_a_declared_shape_too():
    assert Permission.parse({"channel": {"slug": "retail"}, "permissions": []}).channel == "retail"
    with pytest.raises(FieldError):
        Permission.parse({"channel": "retail", "permissions": []}, where="p[0]")
