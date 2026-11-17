"""Plan, approve, apply — and every way that can go wrong.

Each test here corresponds to a bug in the previous implementation. They are the
regression suite for the four defect classes: approval not covering an effect,
a plan that cannot be approved, a no-change run that claims success, and a stale
plan applied after the world moved.
"""
import json

import pytest

from scutch import Unapproved, plan, run

from .conftest import ledger


def test_a_plan_writes_nothing(ws, ops):
    before = (ws / "ledger.json").read_text()
    env = plan(ops["orders.fulfil"].fn, root=ws, inputs={"order_id": "ord_a1"})
    assert env["status"] == "dry"
    assert env["effects"]
    assert (ws / "ledger.json").read_text() == before


def test_the_exact_digest_applies(ws, ops):
    env = plan(ops["orders.fulfil"].fn, root=ws, inputs={"order_id": "ord_a1"})
    out = run(ops["orders.fulfil"].fn, root=ws,
              inputs={"order_id": "ord_a1"}, approve=env["digest"])
    assert out["status"] == "applied"
    assert ledger(ws)["ord_a1"] == "fulfilled"


def test_a_different_digest_is_refused_and_nothing_happens(ws, ops):
    with pytest.raises(Unapproved):
        run(ops["orders.fulfil"].fn, root=ws,
            inputs={"order_id": "ord_a1"}, approve="sha256:nope")
    assert ledger(ws)["ord_a1"] == "placed"


def test_nothing_to_do_plans_no_effects(ws, ops):
    env = plan(ops["orders.fulfil"].fn, root=ws, inputs={"order_id": "ord_a2"})
    assert env["effects"] == []
    assert ledger(ws)["ord_a2"] == "fulfilled"


def test_a_no_change_run_never_reports_a_change(ws, ops):
    """The old design recorded no-change as `applied` and replayed it forever."""
    env = plan(ops["orders.fulfil"].fn, root=ws, inputs={"order_id": "ord_a2"})
    out = run(ops["orders.fulfil"].fn, root=ws,
              inputs={"order_id": "ord_a2"}, approve=env["digest"])
    assert out["status"] == "applied"
    assert out["effects"] == []
    assert out["result"]["changed"] is False


def test_a_plan_cannot_drift_between_approval_and_apply(ws, ops):
    env = plan(ops["orders.fulfil"].fn, root=ws, inputs={"order_id": "ord_a1"})
    data = json.loads((ws / "ledger.json").read_text())
    for order in data["orders"]:
        if order["id"] == "ord_a1":
            order["status"] = "cancelled"
    (ws / "ledger.json").write_text(json.dumps(data))
    with pytest.raises(Unapproved):
        run(ops["orders.fulfil"].fn, root=ws,
            inputs={"order_id": "ord_a1"}, approve=env["digest"])


def test_a_cancelled_plan_can_still_be_approved_later(ws, ops):
    """The old design minted the same digest for a cancelled plan forever, so it
    could never be approved. Here a plan is just a prediction of effects: nothing
    is withdrawn, and the same digest applies when the state is unchanged."""
    env = plan(ops["orders.fulfil"].fn, root=ws, inputs={"order_id": "ord_a1"})
    again = plan(ops["orders.fulfil"].fn, root=ws, inputs={"order_id": "ord_a1"})
    assert again["digest"] == env["digest"]
    out = run(ops["orders.fulfil"].fn, root=ws,
              inputs={"order_id": "ord_a1"}, approve=env["digest"])
    assert out["status"] == "applied"


def test_bulk_covers_every_outstanding_order_in_one_plan(ws, ops):
    env = plan(ops["orders.fulfil_all"].fn, root=ws, inputs={})
    assert len(env["effects"]) == 1          # one write, whatever its size
    out = run(ops["orders.fulfil_all"].fn, root=ws, inputs={}, approve=env["digest"])
    assert sorted(out["result"]["fulfilled"]) == ["ord_a1", "ord_b1"]
    assert ledger(ws)["ord_a1"] == ledger(ws)["ord_b1"] == "fulfilled"
