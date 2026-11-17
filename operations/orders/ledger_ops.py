"""The demo domain: a small order ledger, and the operations that act on it.

Each operation exists to show one property of the *runner*, not one feature of
an order system:

  ``orders.list``     a read: no plan, no approval, no effect
  ``orders.inspect``  a read that can fail with a typed, actionable error
  ``orders.fulfil``   plan -> approve -> apply, bound to a plan digest
  ``orders.bulk``     one plan covering several orders, applied as a unit

The ledger is deliberately boring. If the reader notices the storage, the demo
has failed.
"""

from __future__ import annotations

import dataclasses
from typing import Annotated, Literal

from scutch import Change, Plan, Provider, field, operation, plan_for

LEDGER = Provider("ledger")

OrderId = Annotated[str, field(pattern=r"^ord_[a-z0-9]+$")]
Status = Literal["placed", "fulfilled", "cancelled"]


# --------------------------------------------------------------------------
# Results. These are the public shape; anything not listed never leaves the
# process.


@dataclasses.dataclass
class Order:
    """One order, as a caller sees it."""

    id: str
    customer: str
    status: str
    total: float


@dataclasses.dataclass
class OrderList:
    """A page of orders."""

    orders: list[Order]
    count: int


@dataclasses.dataclass
class Fulfilment:
    """The outcome of fulfilling one order."""

    order_id: str
    status: str
    previous_status: str


@dataclasses.dataclass
class BulkFulfilment:
    """The outcome of fulfilling every outstanding order."""

    fulfilled: list[str]
    remaining: int


# --------------------------------------------------------------------------
# Reads


@operation(
    name="orders.list",
    summary="List orders, optionally filtered by status.",
    effect="read",
    credentials={"ledger": LEDGER},
    examples=[
        {"name": "outstanding", "why": "what still needs doing", "input": {"status": "placed"}},
        {"name": "everything", "why": "the whole ledger", "input": {}},
    ],
)
def list_orders(status: Status | None = None, *, ledger) -> OrderList:
    rows = ledger.all_orders()
    if status is not None:
        rows = [r for r in rows if r.get("status") == status]
    return OrderList(
        orders=[
            Order(
                id=str(r["id"]),
                customer=str(r.get("customer", "")),
                status=str(r.get("status", "")),
                total=float(r.get("total", 0.0)),
            )
            for r in rows
        ],
        count=len(rows),
    )


@operation(
    name="orders.inspect",
    summary="Read one order by id.",
    effect="read",
    credentials={"ledger": LEDGER},
    examples=[{"name": "one", "why": "check its state", "input": {"order_id": "ord_a1"}}],
)
def inspect_order(order_id: OrderId, *, ledger) -> Order:
    row = ledger.get(order_id)  # NotFound is mapped to a hint by the runtime
    return Order(
        id=str(row["id"]),
        customer=str(row.get("customer", "")),
        status=str(row.get("status", "")),
        total=float(row.get("total", 0.0)),
    )


# --------------------------------------------------------------------------
# Plan / apply


@operation(
    name="orders.fulfil",
    summary="Mark one placed order fulfilled.",
    effect="plan_apply",
    credentials={"ledger": LEDGER},
    idempotency_key=["order_id"],
    examples=[{"name": "one", "why": "the common case", "input": {"order_id": "ord_a1"}}],
)
def fulfil_order(order_id: OrderId, *, ledger) -> Fulfilment:
    """Apply. The plan has already confirmed this is the right thing to do."""
    before = ledger.get(order_id)["status"]
    ledger.set_status(order_id, "fulfilled")
    return Fulfilment(order_id=order_id, status="fulfilled", previous_status=before)


@plan_for("orders.fulfil")
def plan_fulfil(order_id: OrderId, *, ledger) -> Plan:
    """Decide whether there is anything to do. Never mutates."""
    current = ledger.get(order_id).get("status")
    if current == "fulfilled":
        return Plan(
            changes=[],
            summary=f"order {order_id} is already fulfilled",
            no_change_result=Fulfilment(
                order_id=order_id, status="fulfilled", previous_status="fulfilled"
            ),
        )
    if current == "cancelled":
        return Plan(
            changes=[],
            summary=f"order {order_id} was cancelled; nothing to fulfil",
            no_change_result=Fulfilment(
                order_id=order_id, status="cancelled", previous_status="cancelled"
            ),
        )
    return Plan(
        changes=[Change(kind="status.change", target=order_id, value="fulfilled")],
        summary=f"mark order {order_id} fulfilled",
    )


@operation(
    name="orders.bulk",
    summary="Fulfil every outstanding order in one plan.",
    effect="plan_apply",
    credentials={"ledger": LEDGER},
    idempotency_key=["scope"],
    examples=[{"name": "all", "why": "end-of-day run", "input": {}}],
)
def fulfil_all(scope: Literal["outstanding"] = "outstanding", *, ledger) -> BulkFulfilment:
    """Apply a plan the caller approved as a single unit."""
    ids = [o["id"] for o in ledger.outstanding()]
    for order_id in ids:
        ledger.set_status(order_id, "fulfilled")
    return BulkFulfilment(fulfilled=list(ids), remaining=len(ledger.outstanding()))


@plan_for("orders.bulk")
def plan_fulfil_all(scope: Literal["outstanding"] = "outstanding", *, ledger) -> Plan:
    rows = ledger.outstanding()
    if not rows:
        return Plan(
            changes=[],
            summary="nothing outstanding",
            no_change_result=BulkFulfilment(fulfilled=[], remaining=0),
        )
    return Plan(
        changes=[
            Change(kind="status.change", target=str(r["id"]), value="fulfilled") for r in rows
        ],
        summary=f"fulfil {len(rows)} outstanding order(s) in one step",
    )
