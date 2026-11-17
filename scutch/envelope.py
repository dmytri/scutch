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


#: Fields that legitimately differ between two runs of the same invocation.
#: Everything else is part of the answer and must be identical — that is what
#: makes a step's output diffable, hashable and cacheable.
VOLATILE = ("run_id", "timing")


def canonical(envelope_obj: dict[str, Any]) -> dict[str, Any]:
    """The reproducible part of an envelope.

    Two runs of the same operation with the same canonical input must produce
    equal canonical forms. `scutch verify` and the test suite assert this, so a
    new field that leaks a clock or a random id fails loudly rather than
    quietly making every step output unique.
    """
    out = dict(envelope_obj)
    for key in VOLATILE:
        out.pop(key, None)
    if isinstance(out.get("next"), dict):
        # A continuation may name this run; the digest (job) is stable, the
        # attempt label is not.
        out["next"] = dict(out["next"])
    return out


def digest(envelope_obj: dict[str, Any]) -> str:
    """Stable hash of an envelope's reproducible content."""
    import hashlib

    body = json.dumps(canonical(envelope_obj), sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(body.encode()).hexdigest()
