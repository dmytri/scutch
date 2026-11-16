"""Plans and approval digests.

A plan is data. Its digest is *derived by the runtime* from the operation
name, the canonical inputs, the idempotency value and the ordered changes.
That means an approval can only ever authorise the exact thing the operator
described — a plan that differs in any byte gets a different digest.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from typing import Any


@dataclasses.dataclass(frozen=True)
class Change:
    """One intended effect."""

    kind: str
    value: Any = None
    target: str | None = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind}
        if self.target is not None:
            out["target"] = self.target
        out["value"] = self.value
        return out


@dataclasses.dataclass
class Plan:
    """What an operation intends to do. Never executes anything itself."""

    changes: list[Change] = dataclasses.field(default_factory=list)
    summary: str = ""
    digest: str | None = None

    def is_empty(self) -> bool:
        return not self.changes

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "changes": [c.to_json() for c in self.changes],
            "digest": self.digest,
        }
        if self.summary:
            out["summary"] = self.summary
        return out

    def diff_lines(self) -> list[str]:
        return [self._line(c) for c in self.changes]

    @staticmethod
    def _line(c: Change) -> str:
        target = f" {c.target}" if c.target else ""
        return f"+ {c.kind}{target} = {json.dumps(c.value, sort_keys=True)}"


def digest_for(
    operation: str,
    inputs: dict[str, Any],
    idempotency: str | None,
    plan: Plan,
) -> str:
    canon = json.dumps(
        {
            "operation": operation,
            "input": inputs,
            "idempotency": idempotency,
            "changes": [c.to_json() for c in plan.changes],
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return "sha256:" + hashlib.sha256(canon.encode()).hexdigest()
