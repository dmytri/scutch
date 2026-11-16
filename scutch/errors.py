"""Error types.

Every failure an operation may raise is a *structured* failure: it carries a
stable code, a human message and optional remediation so an agent can act on it
without parsing prose.
"""

from __future__ import annotations

from typing import Any


class ScutchError(Exception):
    code = "scutch_error"
    exit_code = 1

    def __init__(self, message: str, *, hint: str | None = None, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.details = details or {}

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.hint:
            out["hint"] = self.hint
        if self.details:
            out["details"] = self.details
        return out


class ContractError(ScutchError):
    """The operation implementation violates the contract."""

    code = "contract_error"
    exit_code = 70


class UsageError(ScutchError):
    """The caller used the CLI or supplied inputs wrongly."""

    code = "usage_error"
    exit_code = 2


class NotFound(ScutchError):
    code = "not_found"
    exit_code = 3


class ValidationError(ScutchError):
    """Inputs failed JSON Schema validation."""

    code = "invalid_input"
    exit_code = 4


class ApprovalError(ScutchError):
    """Approval digest does not match the plan about to be applied."""

    code = "approval_mismatch"
    exit_code = 5


class ProviderError(ScutchError):
    """A provider could not be resolved or failed."""

    code = "provider_error"
    exit_code = 6


class OperationFailed(ScutchError):
    """The operation itself reported failure. Not a contract violation."""

    code = "operation_failed"
    exit_code = 7
