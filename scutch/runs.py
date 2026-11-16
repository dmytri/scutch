"""Local run records.

`.scutch/runs/` holds inspectable JSON records, one per attempt, plus an index
keyed by invocation fingerprint. Two properties matter:

* **Durability, not session state.** A record lets a *fresh process* answer
  ``scutch status <id>`` and refuse a duplicate mutation. It never supplies
  hidden conversational context: the canonical inputs to reproduce a call are
  in the envelope's ``next`` block, not in the record.
* **Deleting `.scutch/` is safe.** It removes history and the idempotency
  cache; it never removes the information needed to formulate an invocation.
"""

from __future__ import annotations

import dataclasses
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any


def new_run_id() -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return f"run_{stamp}_{secrets.token_hex(4)}"


def new_job_id() -> str:
    return "job_" + secrets.token_hex(6)


@dataclasses.dataclass
class RunRecord:
    run_id: str
    job_id: str
    operation: str
    status: str
    input: dict[str, Any]
    fingerprint: str
    idempotency: str | None
    plan_digest: str | None
    phase: str
    created_at: float
    updated_at: float
    envelope: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    artifacts: list[str] = dataclasses.field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        out = dataclasses.asdict(self)
        out["created_at_iso"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.created_at))
        out["updated_at_iso"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.updated_at))
        return out


class RunStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    # -- raw io -----------------------------------------------------------

    def _path(self, run_id: str) -> Path:
        return self.root / f"{run_id}.json"

    def save(self, rec: RunRecord) -> None:
        rec.updated_at = time.time()
        payload = rec.to_json()
        tmp = self._path(rec.run_id).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
        os.replace(tmp, self._path(rec.run_id))

    def load(self, run_id: str) -> RunRecord:
        path = self._path(run_id)
        if not path.is_file():
            path = self._path(f"run_{run_id}")
        if not path.is_file():
            raise FileNotFoundError(run_id)
        data = json.loads(path.read_text())
        data.pop("created_at_iso", None)
        data.pop("updated_at_iso", None)
        return RunRecord(**data)

    def find_job(self, job_id: str) -> RunRecord | None:
        for rec in self.recent(limit=None):
            if rec.job_id == job_id:
                return rec
        return None

    def recent(self, limit: int | None = 20) -> list[RunRecord]:
        paths = sorted(self.root.glob("run_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if limit is not None:
            paths = paths[:limit]
        out = []
        for p in paths:
            try:
                out.append(self.load(p.stem))
            except (json.JSONDecodeError, TypeError):
                continue
        return out

    # -- idempotency ------------------------------------------------------

    def find_applied(self, fingerprint: str) -> RunRecord | None:
        """Return a record that already performed this exact mutation."""
        for rec in self.recent(limit=None):
            if rec.fingerprint == fingerprint and rec.phase == "applied" and rec.status in (
                "complete",
                "partial",
            ):
                return rec
        return None

    def find_in_flight(self, fingerprint: str) -> RunRecord | None:
        for rec in self.recent(limit=None):
            if rec.fingerprint == fingerprint and rec.phase in ("applying", "starting"):
                return rec
        return None


def recovery_hint(rec: RunRecord) -> dict[str, Any]:
    """What a caller should do about an interrupted attempt."""
    return {
        "run_id": rec.run_id,
        "job_id": rec.job_id,
        "phase": rec.phase,
        "recommended": [
            ["scutch", "status", rec.job_id, "--format", "json"],
            ["scutch", "recover", rec.run_id, "--format", "json"],
        ],
        "note": "The run was interrupted mid-apply. Verify the external state "
        "before repeating: the idempotency key protects you only on repeat.",
    }
