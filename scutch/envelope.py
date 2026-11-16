"""The envelope: the only thing an operation ever returns to a caller.

Every field the caller needs is here, and nothing it must not see is. The
``next`` block is always a complete, self-sufficient continuation: canonical
inputs plus an argv. A fresh process needs no memory of this one.
"""

from __future__ import annotations

import json
from typing import Any

KIND = "scutch.envelope/v1"


def envelope(
    *,
    operation: str,
    status: str,
    input: dict[str, Any],
    next: dict[str, Any] | None = None,
    plan: dict[str, Any] | None = None,
    result: Any = None,
    artifacts: list[dict[str, Any]] | None = None,
    error: dict[str, Any] | None = None,
    run_id: str | None = None,
    job_id: str | None = None,
    redacted: list[str] | None = None,
    notes: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "kind": KIND,
        "operation": operation,
        "status": status,
        "input": input,
    }
    if plan is not None:
        out["plan"] = plan
    if result is not None:
        out["result"] = result
    if artifacts:
        out["artifacts"] = artifacts
    if redacted:
        out["redacted"] = redacted
    if error is not None:
        out["error"] = error
    out["next"] = next or {}
    if notes:
        out["notes"] = notes
    if run_id:
        out["run_id"] = run_id
    if job_id:
        out["job_id"] = job_id
    if extra:
        out.update(extra)
    return out


def dumps(envelope_obj: dict[str, Any]) -> str:
    return json.dumps(envelope_obj, indent=2, sort_keys=False, default=str)
