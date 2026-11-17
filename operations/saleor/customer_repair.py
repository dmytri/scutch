"""Saleor operations: ordinary typed Python.

Read the signature; everything an agent needs is derived from it.
"""

from __future__ import annotations

import dataclasses
from typing import Annotated, Any, Literal

from scutch import Change, Plan, Provider, field, operation, plan_for, private

from providers.saleor import SaleorApi  # repository-local, loaded by scutch

CustomerId = Annotated[
    str,
    field(pattern=r"^cust_[0-9]+$", description="Saleor user id", example="cust_123"),
]
Permission = Literal["read_orders", "manage_orders", "manage_products"]
Channel = Annotated[str, field(choices=("retail", "wholesale"), description="Channel slug")]

SALEOR = Provider("saleor")


# --------------------------------------------------------------------------
# results


@dataclasses.dataclass
class CustomerSummary:
    """A customer, projected for agent consumption."""

    customer_id: str
    email: str
    channel_permissions: list[dict[str, Any]]


@dataclasses.dataclass
class CustomerList:
    customers: list[CustomerSummary]
    count: int


@dataclasses.dataclass
class RepairReport:
    customer_id: str
    permission: str
    channel: str
    status: Literal["applied", "no_change", "not_found"]
    channel_permissions: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    raw_response: Annotated[dict[str, Any], private()] = dataclasses.field(default_factory=dict)


# --------------------------------------------------------------------------
# operations


@operation(
    name="saleor.customer.list",
    summary="List customers with their channel permissions.",
    effect="read",
    credentials={"api": SALEOR},
    examples=[
        {"name": "first-page", "why": "find a customer id", "input": {"limit": 3}},
    ],
)
def list_customers(limit: int = 20, *, api: SaleorApi) -> CustomerList:
    """Read-only listing. Safe to call from any agent without approval."""
    rows = api.list_customers(limit)
    return CustomerList(
        customers=[
            CustomerSummary(
                customer_id=c.customer_id,
                email=c.email,
                channel_permissions=c.to_json()["channelPermissions"],
            )
            for c in rows
        ],
        count=len(rows),
    )


@operation(
    name="saleor.customer.inspect",
    summary="Inspect one customer's channel permissions and order count.",
    effect="read",
    credentials={"api": SALEOR},
    examples=[
        {"name": "known-customer", "why": "understand current state", "input": {"customer_id": "cust_789"}},
    ],
)
def inspect_customer(customer_id: CustomerId, *, api: SaleorApi) -> CustomerSummary:
    """Read-only. Returns the customer's permissions exactly as Saleor holds them."""
    c = api.get_customer(customer_id)
    return CustomerSummary(
        customer_id=c.customer_id,
        email=c.email,
        channel_permissions=c.to_json()["channelPermissions"],
    )


@operation(
    name="saleor.customer.repair",
    summary="Ensure a customer has one channel permission.",
    effect="plan_apply",
    credentials={"api": SALEOR},
    idempotency_key=["customer_id", "permission", "channel"],
    external_reads=["saleor:user.channelPermissions"],
    intended_effects=["saleor:channelUpdatePermission"],
    examples=[
        {
            "name": "already-correct",
            "why": "shows the no_change path: no approval, no mutation",
            "input": {"customer_id": "cust_123", "permission": "read_orders", "channel": "retail"},
            "expect": {"status": "complete", "result.status": "no_change"},
        },
        {
            "name": "needs-approval",
            "why": "shows plan + digest continuation",
            "input": {"customer_id": "cust_456", "permission": "read_orders", "channel": "retail"},
            "expect": {"status": "needs_approval"},
        },
    ],
)
def repair_customer(
    customer_id: CustomerId,
    permission: Permission,
    channel: Channel = "retail",
    *,
    api: SaleorApi,
) -> RepairReport:
    """Add a permission if it is missing. Applied only after plan approval."""
    before = api.get_customer(customer_id)
    already = permission in before.permissions_on(channel)
    api.apply_permission(customer_id, channel, permission)
    after = api.get_customer(customer_id)
    return RepairReport(
        customer_id=customer_id,
        permission=permission,
        channel=channel,
        status="no_change" if already else "applied",
        channel_permissions=after.to_json()["channelPermissions"],
        raw_response={"before": before.to_json(), "after": after.to_json()},
    )


@plan_for("saleor.customer.repair")
def plan_repair(
    customer_id: CustomerId,
    permission: Permission,
    channel: Channel = "retail",
    *,
    api: SaleorApi,
) -> Plan:
    """The read path. Empty changes mean the caller needs no approval."""
    customer = api.get_customer(customer_id)
    if permission in customer.permissions_on(channel):
        return Plan(
            changes=[],
            summary="permission already present; nothing to do",
            no_change_result=RepairReport(
                customer_id=customer_id,
                permission=permission,
                channel=channel,
                status="no_change",
                channel_permissions=customer.to_json()["channelPermissions"],
            ),
        )
    return Plan(
        changes=[
            Change(
                kind="permission.add",
                target=f"customer:{customer_id}/channel:{channel}",
                value=permission,
            )
        ],
        summary=f"grant {permission} to {customer_id} on {channel}",
    )


def _has(customer: Any, channel: str, permission: str) -> bool:
    """Kept for callers that pass a parsed Customer; delegates to the type."""
    if hasattr(customer, "permissions_on"):
        return permission in customer.permissions_on(channel)
    return False
