"""TUI tests.

The TUI must be a projection, not a reimplementation. These tests prove:

* forms are generated from the same descriptors/JSON Schema the CLI enforces;
* running a form and approving a plan inside the app produces exactly the
  records the CLI produces for the same transitions;
* the app renders both envelopes.
"""

from __future__ import annotations

import json

import pytest

textual = pytest.importorskip("textual")

from scutch.tui import (  # noqa: E402
    FormField,
    ScutchApp,
    build_form,
    describe_card,
    envelope_markdown,
    field_id,
    form_to_input,
    slug,
    unslug,
)


@pytest.fixture()
def ws(workspace):
    from scutch.workspace import load_workspace

    return load_workspace(workspace)


def test_widget_slugs_round_trip():
    for name in ("saleor.customer.repair", "a.b-c.d", "reports.weekly"):
        assert unslug(slug(name)) == name
        assert "." not in slug(name)


def test_form_is_generated_from_the_contract(ws):
    op = ws.registry.get("saleor.customer.repair")
    fields = build_form(op)
    assert [f.name for f in fields] == ["customer_id", "permission", "channel"]
    assert [f.required for f in fields] == [True, True, False]
    by_name = {f.name: f for f in fields}
    assert by_name["permission"].kind == "enum"
    assert by_name["channel"].enum == ["retail", "wholesale"]
    assert by_name["channel"].default == "retail"


def test_form_input_is_typed_and_optional_defaults_drop_out(ws):
    op = ws.registry.get("saleor.customer.repair")
    fields = build_form(op)
    fields[0].value = " cust_456 "
    fields[1].value = "read_orders"
    fields[2].value = ""
    assert form_to_input(fields) == {"customer_id": "cust_456", "permission": "read_orders"}


def test_blank_required_field_is_rejected(ws):
    op = ws.registry.get("saleor.customer.repair")
    fields = build_form(op)
    fields[1].value = "read_orders"
    with pytest.raises(ValueError, match="customer_id"):
        form_to_input(fields)


def test_card_shows_handles_not_secrets(ws):
    card = describe_card(ws.registry.get("saleor.customer.repair"))
    assert "saleor" in card
    assert "SCUTCH_SALEOR_TOKEN" not in card
    assert "permission.add" not in card


def test_envelope_markdown_carries_plan_digest_and_command(ws):
    from scutch.runner import execute

    env, _ = execute(
        ws, "saleor.customer.repair", {"customer_id": "cust_456", "permission": "read_orders"}
    )
    md = envelope_markdown(env)
    assert "needs_approval" in md
    assert env["plan"]["digest"] in md
    assert "scutch run saleor.customer.repair" in md
    assert "read_orders" in md


@pytest.mark.asyncio
async def test_app_approves_a_plan_through_the_same_lifecycle(ws):
    app = ScutchApp(ws)
    async with app.run_test() as pilot:
        app.select_operation("saleor.customer.repair")
        await pilot.pause()
        app.query_one(f"#{field_id('customer_id', app._form_generation)}").value = "cust_456"  # type: ignore[attr-defined]
        app.query_one(f"#{field_id('permission', app._form_generation)}").value = "read_orders"  # type: ignore[attr-defined]
        app.query_one("#idem").value = "ui-test-key"  # type: ignore[attr-defined]
        await pilot.pause()

        await pilot.press("r")
        await pilot.pause()
        preview = app.last_envelope
        assert preview is not None
        assert preview["status"] == "needs_approval"
        assert app.pending_approval == preview["plan"]["digest"]
        assert not app.query_one("#approve").disabled  # type: ignore[attr-defined]

        await pilot.press("a")
        await pilot.pause()
        applied = app.last_envelope
        assert applied is not None
        assert applied["status"] == "complete"
        assert applied["result"]["status"] == "applied"

        # The record is the same shape the CLI writes.
        runs = [json.loads(p.read_text()) for p in (ws.runs_dir).glob("run_*.json")]
        applied_runs = [r for r in runs if r["phase"] == "applied"]
        assert len(applied_runs) == 1
        assert applied_runs[0]["idempotency"] == "ui-test-key"
        assert applied_runs[0]["plan_digest"] == preview["plan"]["digest"]


@pytest.mark.asyncio
async def test_app_refuses_approval_without_a_plan(ws):
    app = ScutchApp(ws)
    async with app.run_test() as pilot:
        app.select_operation("saleor.customer.repair")
        await pilot.pause()
        assert app.query_one("#approve").disabled  # type: ignore[attr-defined]
        app.action_approve()
        await pilot.pause()
        assert app.last_envelope is None


@pytest.mark.asyncio
async def test_app_shows_run_history(ws):
    from scutch.runner import execute

    execute(ws, "saleor.customer.list", {})
    app = ScutchApp(ws)
    async with app.run_test() as pilot:
        app.action_inspect_runs()
        await pilot.pause()
        log = app.query_one("#log", textual.widgets.RichLog)
        rendered = "\n".join(line.text for line in log.lines)
        assert "saleor.customer.list" in rendered


@pytest.mark.asyncio
async def test_app_starts_on_a_usable_operation(ws):
    """No dead initial state: the catalogue is populated and one card is open."""
    app = ScutchApp(ws)
    async with app.run_test() as pilot:
        await pilot.pause()
        lv = app.query_one("#ops", textual.widgets.ListView)
        assert len(lv.children) == 3
        assert app.current is not None
        assert app.current.name == ws.registry.names()[0]
        assert app.fields, "form was not generated for the initial operation"
        assert app.query_one("#approve").disabled is True  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_selection_switches_the_form(ws):
    app = ScutchApp(ws)
    async with app.run_test() as pilot:
        app.select_operation("saleor.customer.list")
        await pilot.pause()
        assert [f.name for f in app.fields] == ["limit"]
        app.select_operation("saleor.customer.repair")
        await pilot.pause()
        assert [f.name for f in app.fields] == ["customer_id", "permission", "channel"]
