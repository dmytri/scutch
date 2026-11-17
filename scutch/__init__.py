"""Scutch — a local-first catalogue of typed operations.

Public surface for operation authors::

    from scutch import operation, plan_for, Plan, Change, field, private

    @operation(name="x.y", summary="...", effect="plan_apply",
               credentials={"api": Provider("svc")},
               idempotency_key=["id"])
    def y(id: str, *, api) -> Report: ...

    @plan_for("x.y")
    def plan_y(id: str, *, api) -> Plan: ...
"""

from .contract import ProviderRef as Provider
from .contract import operation, plan_for
from .errors import (
    ApprovalError,
    ContractError,
    NotFound,
    OperationFailed,
    ProviderError,
    ScutchError,
    UsageError,
    ValidationError,
)
from .gherkin import Scenario, Step, execute, parse_feature, step
from .plan import Change, Plan
from .schema import Private, field, private
from .runner import RunContext

__all__ = [
    "Provider",
    "operation",
    "plan_for",
    "Plan",
    "Change",
    "parse_feature",
    "step",
    "execute",
    "RunContext",
    "field",
    "private",
    "Private",
    "ScutchError",
    "ContractError",
    "UsageError",
    "NotFound",
    "ValidationError",
    "ApprovalError",
    "ProviderError",
    "OperationFailed",
]
__version__ = "0.1.0"
