"""Invocation: validation, plan/apply lifecycle, idempotency, envelopes.

Transition table (one lifecycle, two interfaces — the TUI calls exactly the
same functions the CLI does):

    run ──missing required─▶ needs_input
    run ──plan has changes─▶ needs_approval  (next.argv carries --approve-plan)
    run ──approved digest──▶ running ──▶ complete | partial | failed
    run ──plan empty───────▶ complete (no_change, no mutation requested)
    run ──already applied──▶ complete (replayed, no second mutation)
    run ──interrupted──────▶ partial (recovery hint, explicit commands)
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import time
import traceback
from pathlib import Path
from typing import Any

import jsonschema

from .contract import Invocation, Operation, ProviderRef
from .envelope import envelope
from .errors import ApprovalError, ContractError, NotFound, OperationFailed, ValidationError
from .plan import Plan, digest_for
from .providers import resolve
from .runs import RunRecord, RunStore, new_job_id, new_run_id, recovery_hint
from .schema import to_public


@dataclasses.dataclass
class RunContext:
    """Optional injected helper for operations that emit artifacts."""

    workspace_root: Path
    artifacts_dir: Path
    run_id: str
    job_id: str
    operation: Operation

    def artifact(self, name: str, payload: Any, *, filename: str | None = None) -> str:
        """Write a declared artifact. Undeclared names are a contract error."""
        if name not in self.operation.artifacts:
            raise ContractError(
                f"{self.operation.name} wrote undeclared artifact {name!r}",
                hint="declare it in @operation(artifacts=[...])",
            )
        target_dir = self.artifacts_dir / self.run_id
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / (filename or f"{name}.json")
        if isinstance(payload, (dict, list)):
            path.write_text(json.dumps(payload, indent=2, default=str))
        else:
            path.write_text(str(payload))
        return str(path)


def _next_run_argv(op: Operation, *, format: str, idempotency: str | None, approve: str | None) -> list[str]:
    argv = ["scutch", "run", op.name, "--input", "@-", "--format", format]
    if idempotency:
        argv += ["--idempotency-key", idempotency]
    if approve:
        argv += ["--approve-plan", approve]
    return argv


def _call(fn, op: Operation, inputs: dict[str, Any], kwargs: dict[str, Any]):
    """Call an operation/plan function, injecting providers and context."""
    sig = inspect.signature(fn)
    args = dict(inputs)
    if "api" in sig.parameters:
        refs = op.credentials
        if len(refs) != 1:
            raise ContractError(f"{op.name}: api injection needs exactly one credential")
        (alias, ref), = refs.items()
        args["api"] = kwargs["providers"][ref.name].load()
    if "ctx" in sig.parameters:
        args["ctx"] = kwargs["ctx"]
    return fn(**args)


def validate_input(op: Operation, raw: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
    """Return (canonical, missing_required, violations)."""
    schema = op.input_schema()
    canonical = op.canonical_inputs()
    canonical.update(raw)
    missing = [k for k in schema.get("required", []) if canonical.get(k) is None]
    violations: list[dict[str, Any]] = []
    if not missing:
        validator = jsonschema.Draft202012Validator(schema)
        for err in sorted(validator.iter_errors(canonical), key=lambda e: list(e.path)):
            violations.append(
                {
                    "path": "/".join(str(p) for p in err.path) or "<root>",
                    "message": err.message,
                    "validator": err.validator,
                }
            )
    return canonical, missing, violations


def execute(
    ws,
    op_name: str,
    raw_input: dict[str, Any],
    *,
    approve_plan: str | None = None,
    idempotency_key: str | None = None,
    fmt: str = "json",
) -> tuple[dict[str, Any], int]:
    """Perform one lifecycle transition. Returns (envelope, exit_code)."""
    op = ws.registry.get(op_name)
    store = RunStore(ws.runs_dir)
    canonical, missing, violations = validate_input(op, raw_input)
    # An omitted key must not be shared by unrelated calls: derive a stable one
    # from the canonical inputs. Only an explicit key can ever collide.
    idem = idempotency_key or raw_input.get("_idempotency_key") or _auto_key(op, canonical)

    # 1. needs_input -----------------------------------------------------
    if missing:
        sub_schema = {
            "type": "object",
            "properties": {k: op.input_schema()["properties"][k] for k in missing},
            "required": missing,
        }
        env = envelope(
            operation=op.name,
            status="needs_input",
            input=canonical,
            next={
                "argv": _next_run_argv(op, format=fmt, idempotency=idem, approve=None),
                "input": canonical,
                "note": "supply the missing fields as JSON on stdin",
            },
            error={
                "code": "missing_input",
                "message": "required inputs are missing",
                "details": {"missing": missing, "schema": sub_schema},
            },
        )
        return env, 0

    # 2. invalid ---------------------------------------------------------
    if violations:
        env = envelope(
            operation=op.name,
            status="failed",
            input=canonical,
            next={
                "argv": _next_run_argv(op, format=fmt, idempotency=idem, approve=None),
                "input": canonical,
            },
            error={
                "code": "invalid_input",
                "message": "inputs failed schema validation",
                "details": {"violations": violations, "schema": op.input_schema()},
            },
        )
        return env, ValidationError.exit_code

    conv = Invocation(op, canonical)
    fingerprint = conv.fingerprint()

    # 3. idempotency conflicts -------------------------------------------
    if op.idempotency_key:
        for rec in store.recent(limit=None):
            # A preview reserves nothing: it produced no effect. Only attempts
            # that reached the apply path own an idempotency key.
            if rec.phase in ("planned", "needs_approval"):
                continue
            if rec.idempotency == idem and rec.fingerprint != fingerprint:
                env = envelope(
                    operation=op.name,
                    status="failed",
                    input=canonical,
                    next={"argv": _next_run_argv(op, format=fmt, idempotency=idem, approve=None), "input": canonical},
                    error={
                        "code": "idempotency_conflict",
                        "message": f"idempotency key {idem!r} was already used with different inputs",
                        "details": {"existing_run": rec.run_id, "existing_input": rec.input},
                        "hint": "use a new key that describes this intent, or reuse the original input",
                    },
                )
                return env, 9
        # Replay is an *explicit-key* promise. Without one, identical inputs are
        # simply a new invocation: it re-plans and observes current state.
        explicit = idempotency_key is not None or "_idempotency_key" in raw_input
        for rec in store.recent(limit=None):
            if rec.phase == "cancelled" and rec.plan_digest and rec.plan_digest == (approve_plan or ""):
                return envelope(
                    operation=op.name,
                    status="cancelled",
                    input=canonical,
                    next={
                        "argv": ["scutch", "run", op.name, "--input", "@-", "--format", fmt],
                        "input": canonical,
                        "note": "this plan was cancelled; preview again to get a fresh plan and digest",
                    },
                    run_id=rec.run_id,
                    job_id=rec.job_id,
                    error={
                        "code": "plan_cancelled",
                        "message": "the approved digest belongs to a cancelled plan",
                        "details": {"cancelled_run": rec.run_id},
                        "hint": "re-preview to obtain a new plan; nothing was applied",
                    },
                ), 10
        rec_applied = store.find_applied(fingerprint) if explicit else None
        if rec_applied is not None:
            replay = rec_applied.envelope or {}
            env = envelope(
                operation=op.name,
                status="complete",
                input=canonical,
                result=replay.get("result"),
                plan=replay.get("plan"),
                redacted=replay.get("redacted"),
                next={
                    "argv": ["scutch", "status", rec_applied.job_id, "--format", fmt],
                    "input": canonical,
                    "note": "already applied; no mutation was requested. This is the recorded result.",
                },
                run_id=rec_applied.run_id,
                job_id=rec_applied.job_id,
                notes=["replayed_from=" + rec_applied.run_id],
                extra={
                    "replay": {
                        "of_run": rec_applied.run_id,
                        "recorded_status": rec_applied.status,
                        "mutation_requested": False,
                        "note": "this invocation already took effect; no new mutation was issued",
                    }
                },
            )
            return env, 0
        rec_flight = store.find_in_flight(fingerprint)
        if rec_flight is not None:
            env = envelope(
                operation=op.name,
                status="partial",
                input=canonical,
                next=recovery_hint(rec_flight),
                run_id=rec_flight.run_id,
                job_id=rec_flight.job_id,
                error={
                    "code": "in_flight",
                    "message": "an identical invocation is recorded but never finished",
                    "details": {"phase": rec_flight.phase, "run_id": rec_flight.run_id},
                },
            )
            return env, 8

    run_id = new_run_id()
    job_id = new_job_id()
    ctx = RunContext(ws.root, ws.artifacts_dir, run_id, job_id, op)
    providers = {
        name: resolve(cfg, ws.root) for name, cfg in ws.providers.items()
    }
    for ref in op.credentials.values():
        if ref.name not in providers:
            env = envelope(
                operation=op.name,
                status="failed",
                input=canonical,
                next={"argv": _next_run_argv(op, format=fmt, idempotency=idem, approve=None), "input": canonical},
                error={
                    "code": "provider_not_configured",
                    "message": f"provider {ref.name!r} is not declared in scutch.toml",
                },
            )
            return env, 6

    # 4. plan ------------------------------------------------------------
    plan_obj: Plan | None = None
    if op.plan_fn is not None:
        try:
            plan_obj = _call(op.plan_fn, op, canonical, {"providers": providers, "ctx": ctx})
        except Exception as exc:  # noqa: BLE001 - provider/operation failure is data
            return _failure_envelope(op, canonical, idem, fmt, exc, run_id, job_id), 7
        if not isinstance(plan_obj, Plan):
            raise ContractError(
                f"{op.name}: plan function must return scutch.plan.Plan, got {type(plan_obj).__name__}"
            )
        plan_obj.digest = digest_for(op.name, canonical, conv.idempotency_value(), plan_obj)

    # 4a. nothing to do --------------------------------------------------
    if plan_obj is not None and plan_obj.is_empty():
        rec = RunRecord(
            run_id=run_id,
            job_id=job_id,
            operation=op.name,
            status="complete",
            input=canonical,
            fingerprint=fingerprint,
            idempotency=idem if op.idempotency_key else None,
            plan_digest=plan_obj.digest,
            phase="applied",
            created_at=time.time(),
            updated_at=time.time(),
        )
        result_payload, redacted = _safe_result(op, plan_obj)
        rec.envelope = envelope(
            operation=op.name,
            status="complete",
            input=canonical,
            plan=plan_obj.to_json(),
            result=result_payload,
            redacted=redacted,
            next={"argv": ["scutch", "status", job_id, "--format", fmt], "input": canonical},
            run_id=run_id,
            job_id=job_id,
        )
        store.save(rec)
        return rec.envelope, 0

    # 4b. approval required ----------------------------------------------
    if plan_obj is not None and op.requires_approval and approve_plan != plan_obj.digest:
        if approve_plan is not None:
            return _approval_mismatch(op, canonical, idem, fmt, plan_obj, approve_plan, run_id, job_id)
        rec = RunRecord(
            run_id=run_id,
            job_id=job_id,
            operation=op.name,
            status="needs_approval",
            input=canonical,
            fingerprint=fingerprint,
            idempotency=idem if op.idempotency_key else None,
            plan_digest=plan_obj.digest,
            phase="planned",
            created_at=time.time(),
            updated_at=time.time(),
        )
        env = envelope(
            operation=op.name,
            status="needs_approval",
            input=canonical,
            plan=plan_obj.to_json(),
            next={
                "argv": _next_run_argv(op, format=fmt, idempotency=idem, approve=plan_obj.digest),
                "input": canonical,
                "plan_digest": plan_obj.digest,
            },
            run_id=run_id,
            job_id=job_id,
            notes=["approval is bound to this digest; any change to inputs or plan yields a new one"],
        )
        rec.envelope = env
        store.save(rec)
        return env, 0

    # 5. apply -----------------------------------------------------------
    rec = RunRecord(
        run_id=run_id,
        job_id=job_id,
        operation=op.name,
        status="running",
        input=canonical,
        fingerprint=fingerprint,
        idempotency=idem if op.idempotency_key else None,
        plan_digest=plan_obj.digest if plan_obj else None,
        phase="applying",
        created_at=time.time(),
        updated_at=time.time(),
    )
    store.save(rec)
    started = time.time()
    try:
        raw_result = _call(op.fn, op, canonical, {"providers": providers, "ctx": ctx})
    except Exception as exc:  # noqa: BLE001
        rec.status = "failed"
        rec.phase = "failed"
        rec.error = _exc_json(exc)
        env = _failure_envelope(op, canonical, idem, fmt, exc, run_id, job_id)
        rec.envelope = env
        store.save(rec)
        return env, 7

    result_payload, redacted = to_public(raw_result)
    artifacts = _collect_artifacts(ctx)
    env = envelope(
        operation=op.name,
        status="complete",
        input=canonical,
        plan=plan_obj.to_json() if plan_obj else None,
        result=result_payload,
        redacted=redacted,
        artifacts=artifacts,
        next={
            "argv": ["scutch", "status", job_id, "--format", fmt],
            "input": canonical,
            "repeat": _next_run_argv(op, format=fmt, idempotency=idem, approve=None),
            "note": "to repeat deliberately, reuse this argv; the idempotency key prevents a second mutation",
        },
        run_id=run_id,
        job_id=job_id,
        extra={"elapsed_ms": int((time.time() - started) * 1000)},
    )
    rec.status = "complete"
    rec.phase = "applied"
    rec.artifacts = [a["path"] for a in artifacts]
    rec.envelope = env
    store.save(rec)
    superseded = store.supersede_planned(rec.plan_digest, rec.run_id)
    if superseded:
        env.setdefault("notes", []).append(
            "superseded_previews=" + ",".join(superseded)
        )
        rec.envelope = env
        store.save(rec)
    return env, 0


# --------------------------------------------------------------------------
# helpers


def _auto_key(op: Operation, canonical: dict[str, Any]) -> str:
    import hashlib

    canon = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return f"auto:{op.name}:{hashlib.sha256(canon.encode()).hexdigest()[:16]}"


def _exception_hint(exc: Exception) -> str | None:
    return getattr(exc, "hint", None)


def _exc_json(exc: Exception) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": getattr(exc, "code", "operation_failed"),
        "message": str(exc),
        "type": type(exc).__name__,
    }
    hint = _exception_hint(exc)
    if hint:
        payload["hint"] = hint
    details = getattr(exc, "details", None)
    if details:
        payload["details"] = details
    return payload


def _failure_envelope(op, canonical, idem, fmt, exc, run_id, job_id) -> dict[str, Any]:
    return envelope(
        operation=op.name,
        status="failed",
        input=canonical,
        next={
            "argv": _next_run_argv(op, format=fmt, idempotency=idem, approve=None),
            "input": canonical,
            "note": "fix the reported condition, then retry the same invocation",
        },
        error=_exc_json(exc),
        run_id=run_id,
        job_id=job_id,
        notes=["trace" in str(exc).lower() and "see run record" or "see run record"],
    )


def _approval_mismatch(op, canonical, idem, fmt, plan_obj, supplied, run_id, job_id):
    env = envelope(
        operation=op.name,
        status="needs_approval",
        input=canonical,
        plan=plan_obj.to_json(),
        next={
            "argv": _next_run_argv(op, format=fmt, idempotency=idem, approve=plan_obj.digest),
            "input": canonical,
            "plan_digest": plan_obj.digest,
        },
        error={
            "code": "approval_mismatch",
            "message": "approved digest does not match the current plan",
            "details": {"supplied": supplied, "current": plan_obj.digest},
            "hint": "nothing was applied; approve the digest returned here",
        },
        run_id=run_id,
        job_id=job_id,
    )
    return env, ApprovalError.exit_code


def _collect_artifacts(ctx: RunContext) -> list[dict[str, Any]]:
    out = []
    base = ctx.artifacts_dir / ctx.run_id
    if base.is_dir():
        for f in sorted(base.iterdir()):
            out.append(
                {
                    "name": f.stem,
                    "path": str(f.relative_to(ctx.workspace_root)),
                    "restricted": True,
                    "bytes": f.stat().st_size,
                }
            )
    return out


def _safe_result(op: Operation, plan_obj: Plan | None) -> tuple[Any, list[str]]:
    """Result for an empty plan: the plan's observation, or a schema-shaped stub.

    The operation's apply path is never entered, so no mutation can be issued
    on a no-change transition.
    """
    if plan_obj is not None and plan_obj.no_change_result is not None:
        payload, redacted = to_public(plan_obj.no_change_result)
        return payload, redacted
    stub = _stub_from_schema(op.output_schema())
    stub.setdefault("status", "no_change")
    return stub, []


def _stub_from_schema(schema: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, prop in (schema.get("properties") or {}).items():
        tp = prop.get("type")
        if tp == "array":
            out[name] = []
        elif tp == "object":
            out[name] = {}
        elif "enum" in prop:
            out[name] = prop["enum"][0]
        elif tp == "string":
            out[name] = ""
        elif tp == "integer":
            out[name] = 0
        elif tp == "boolean":
            out[name] = False
        elif tp == "number":
            out[name] = 0.0
        else:
            out[name] = None
    return out


def cancel(ws, job_or_run: str, fmt: str = "json") -> tuple[dict[str, Any], int]:
    """Cancel a pending plan. Only plans that have not applied anything.

    A cancelled plan can never be approved: its digest is dead. Nothing is
    "rolled back", because a `needs_approval` envelope has not mutated anything.
    """
    store = RunStore(ws.runs_dir)
    rec = store.find_job(job_or_run) or _load_or_none(store, job_or_run)
    if rec is None:
        raise NotFound(f"no run or job {job_or_run!r}", hint="run `scutch runs --format json`")

    if rec.phase == "applied" and rec.plan_digest:
        # A consequential run that already took effect: cancelling is too late,
        # and Scutch will not pretend otherwise.
        return envelope(
            operation=rec.operation,
            status="complete",
            input=rec.input,
            next={"argv": ["scutch", "status", rec.job_id, "--format", fmt], "input": rec.input},
            run_id=rec.run_id,
            job_id=rec.job_id,
            error={
                "code": "already_applied",
                "message": "the plan already took effect; it cannot be cancelled",
                "hint": "reverse it with the operation's inverse, if one exists",
            },
        ), 11
    if rec.phase not in ("planned",):
        return envelope(
            operation=rec.operation,
            status=rec.status,
            input=rec.input,
            next={"argv": ["scutch", "status", rec.job_id, "--format", fmt], "input": rec.input},
            run_id=rec.run_id,
            job_id=rec.job_id,
            error={
                "code": "not_cancellable",
                "message": f"run is {rec.phase!r}, which has no pending plan",
            },
        ), 12

    rec.status = "cancelled"
    rec.phase = "cancelled"
    rec.envelope = envelope(
        operation=rec.operation,
        status="cancelled",
        input=rec.input,
        plan=(rec.envelope or {}).get("plan"),
        next={
            "argv": ["scutch", "run", rec.operation, "--input", "@-", "--format", fmt],
            "input": rec.input,
            "note": "cancelled; nothing was applied. Run this to preview again.",
        },
        run_id=rec.run_id,
        job_id=rec.job_id,
        notes=["cancelled before approval; the plan digest is no longer valid"],
    )
    store.save(rec)
    return rec.envelope, 0


def _load_or_none(store: RunStore, run_id: str):
    try:
        return store.load(run_id)
    except FileNotFoundError:
        return None


def status(ws, job_or_run: str, fmt: str = "json") -> tuple[dict[str, Any], int]:
    store = RunStore(ws.runs_dir)
    rec = store.find_job(job_or_run)
    if rec is None:
        try:
            rec = store.load(job_or_run)
        except FileNotFoundError:
            raise NotFound(f"no run or job {job_or_run!r}", hint="run `scutch runs --format json`")
    body = {
        "kind": "scutch.status/v1",
        "job_id": rec.job_id,
        "run_id": rec.run_id,
        "operation": rec.operation,
        "status": rec.status,
        "phase": rec.phase,
        "input": rec.input,
        "plan_digest": rec.plan_digest,
        "artifacts": rec.artifacts,
        "created_at": rec.to_json()["created_at_iso"],
        "updated_at": rec.to_json()["updated_at_iso"],
        "result": (rec.envelope or {}).get("result"),
        "error": rec.error,
    }
    if rec.phase in ("applying", "starting"):
        body["next"] = recovery_hint(rec)
    elif rec.phase == "superseded":
        body["superseded_by"] = rec.superseded_by
        body["next"] = {
            "argv": ["scutch", "status", rec.superseded_by, "--format", fmt],
            "input": rec.input,
            "note": "this preview was applied; inspect the applied run instead",
        }
    elif rec.phase == "cancelled":
        body["next"] = {
            "argv": ["scutch", "run", rec.operation, "--input", "@-", "--format", fmt],
            "input": rec.input,
            "note": "this plan was cancelled; nothing was applied",
        }
    elif rec.phase == "planned":
        body["next"] = {
            "argv": ["scutch", "run", rec.operation, "--input", "@-", "--format", fmt,
                     "--approve-plan", rec.plan_digest],
            "input": rec.input,
            "plan_digest": rec.plan_digest,
            "note": "this plan is still pending; approving it applies exactly these changes",
            "cancel": ["scutch", "cancel", rec.job_id, "--format", fmt],
        }
    else:
        body["next"] = {
            "argv": ["scutch", "run", rec.operation, "--input", "@-", "--format", fmt],
            "input": rec.input,
        }
    return body, 0

"""Invocation: validation, plan/apply lifecycle, idempotency, envelopes.

Transition table (one lifecycle, two interfaces — the TUI calls exactly the
same functions the CLI does):

    run ──missing required─▶ needs_input
    run ──plan has changes─▶ needs_approval  (next.argv carries --approve-plan)
    run ──approved digest──▶ running ──▶ complete | partial | failed
    run ──plan empty───────▶ complete (no_change, no mutation requested)
    run ──already applied──▶ complete (replayed, no second mutation)
    run ──interrupted──────▶ partial (recovery hint, explicit commands)
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import time
import traceback
from pathlib import Path
from typing import Any

import jsonschema

from .contract import Invocation, Operation, ProviderRef
from .envelope import envelope
from .errors import (
    ApprovalError,
    ContractError,
    NotFound,
    OperationFailed,
    ProviderError,
    ValidationError,
)
from .plan import Plan, digest_for
from .providers import resolve
from .runs import RunRecord, RunStore, new_run_id, recovery_hint, stable_job_id
from .schema import to_public


@dataclasses.dataclass
class RunContext:
    """Optional injected helper for operations that emit artifacts."""

    workspace_root: Path
    artifacts_dir: Path
    run_id: str
    job_id: str
    operation: Operation

    def artifact(self, name: str, payload: Any, *, filename: str | None = None) -> str:
        """Write a declared artifact. Undeclared names are a contract error."""
        if name not in self.operation.artifacts:
            raise ContractError(
                f"{self.operation.name} wrote undeclared artifact {name!r}",
                hint="declare it in @operation(artifacts=[...])",
            )
        target_dir = self.artifacts_dir / self.run_id
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / (filename or f"{name}.json")
        if isinstance(payload, (dict, list)):
            path.write_text(json.dumps(payload, indent=2, default=str))
        else:
            path.write_text(str(payload))
        return str(path)


def _next_run_argv(op: Operation, *, format: str, idempotency: str | None, approve: str | None) -> list[str]:
    argv = ["scutch", "run", op.name, "--input", "@-", "--format", format]
    if idempotency:
        argv += ["--idempotency-key", idempotency]
    if approve:
        argv += ["--approve-plan", approve]
    return argv


def _call(fn, op: Operation, inputs: dict[str, Any], kwargs: dict[str, Any]):
    """Call an operation/plan function, injecting providers and context."""
    sig = inspect.signature(fn)
    args = dict(inputs)
    if "api" in sig.parameters:
        refs = op.credentials
        if len(refs) != 1:
            raise ContractError(f"{op.name}: api injection needs exactly one credential")
        (alias, ref), = refs.items()
        args["api"] = kwargs["providers"][ref.name].load()
    if "ctx" in sig.parameters:
        args["ctx"] = kwargs["ctx"]
    return fn(**args)


def validate_input(op: Operation, raw: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
    """Return (canonical, missing_required, violations)."""
    schema = op.input_schema()
    canonical = op.canonical_inputs()
    canonical.update(raw)
    missing = [k for k in schema.get("required", []) if canonical.get(k) is None]
    violations: list[dict[str, Any]] = []
    if not missing:
        validator = jsonschema.Draft202012Validator(schema)
        for err in sorted(validator.iter_errors(canonical), key=lambda e: list(e.path)):
            violations.append(
                {
                    "path": "/".join(str(p) for p in err.path) or "<root>",
                    "message": err.message,
                    "validator": err.validator,
                }
            )
    return canonical, missing, violations


def execute(
    ws,
    op_name: str,
    raw_input: dict[str, Any],
    *,
    approve_plan: str | None = None,
    idempotency_key: str | None = None,
    fmt: str = "json",
) -> tuple[dict[str, Any], int]:
    """Perform one lifecycle transition. Returns (envelope, exit_code)."""
    op = ws.registry.get(op_name)
    store = RunStore(ws.runs_dir)
    canonical, missing, violations = validate_input(op, raw_input)
    # An omitted key must not be shared by unrelated calls: derive a stable one
    # from the canonical inputs. Only an explicit key can ever collide.
    idem = idempotency_key or raw_input.get("_idempotency_key") or _auto_key(op, canonical)

    # 1. needs_input -----------------------------------------------------
    if missing:
        sub_schema = {
            "type": "object",
            "properties": {k: op.input_schema()["properties"][k] for k in missing},
            "required": missing,
        }
        env = envelope(
            operation=op.name,
            status="needs_input",
            input=canonical,
            next={
                "argv": _next_run_argv(op, format=fmt, idempotency=idem, approve=None),
                "input": canonical,
                "note": "supply the missing fields as JSON on stdin",
            },
            error={
                "code": "missing_input",
                "message": "required inputs are missing",
                "details": {"missing": missing, "schema": sub_schema},
            },
        )
        return env, 0

    # 2. invalid ---------------------------------------------------------
    if violations:
        env = envelope(
            operation=op.name,
            status="failed",
            input=canonical,
            next={
                "argv": _next_run_argv(op, format=fmt, idempotency=idem, approve=None),
                "input": canonical,
            },
            error={
                "code": "invalid_input",
                "message": "inputs failed schema validation",
                "details": {"violations": violations, "schema": op.input_schema()},
            },
        )
        return env, ValidationError.exit_code

    conv = Invocation(op, canonical)
    fingerprint = conv.fingerprint()

    # 3. idempotency conflicts -------------------------------------------
    if op.idempotency_key:
        for rec in store.recent(limit=None):
            # A preview reserves nothing: it produced no effect. Only attempts
            # that reached the apply path own an idempotency key.
            if rec.phase in ("planned", "needs_approval"):
                continue
            if rec.idempotency == idem and rec.fingerprint != fingerprint:
                env = envelope(
                    operation=op.name,
                    status="failed",
                    input=canonical,
                    next={"argv": _next_run_argv(op, format=fmt, idempotency=idem, approve=None), "input": canonical},
                    error={
                        "code": "idempotency_conflict",
                        "message": f"idempotency key {idem!r} was already used with different inputs",
                        "details": {"existing_run": rec.run_id, "existing_input": rec.input},
                        "hint": "use a new key that describes this intent, or reuse the original input",
                    },
                )
                return env, 9
        # Replay is an *explicit-key* promise. Without one, identical inputs are
        # simply a new invocation: it re-plans and observes current state.
        explicit = idempotency_key is not None or "_idempotency_key" in raw_input
        for rec in store.recent(limit=None):
            if rec.phase == "cancelled" and rec.plan_digest and rec.plan_digest == (approve_plan or ""):
                return envelope(
                    operation=op.name,
                    status="cancelled",
                    input=canonical,
                    next={
                        "argv": ["scutch", "run", op.name, "--input", "@-", "--format", fmt],
                        "input": canonical,
                        "note": "this plan was cancelled; preview again to get a fresh plan and digest",
                    },
                    run_id=rec.run_id,
                    job_id=rec.job_id,
                    error={
                        "code": "plan_cancelled",
                        "message": "the approved digest belongs to a cancelled plan",
                        "details": {"cancelled_run": rec.run_id},
                        "hint": "re-preview to obtain a new plan; nothing was applied",
                    },
                ), 10
        rec_applied = store.find_applied(fingerprint) if explicit else None
        if rec_applied is not None:
            replay = rec_applied.envelope or {}
            env = envelope(
                operation=op.name,
                status="complete",
                input=canonical,
                result=replay.get("result"),
                plan=replay.get("plan"),
                redacted=replay.get("redacted"),
                next={
                    "argv": ["scutch", "status", rec_applied.job_id, "--format", fmt],
                    "input": canonical,
                    "note": "already applied; no mutation was requested. This is the recorded result.",
                },
                run_id=rec_applied.run_id,
                job_id=rec_applied.job_id,
                notes=["replayed_from=" + rec_applied.run_id],
                extra={
                    "replay": {
                        "of_run": rec_applied.run_id,
                        "recorded_status": rec_applied.status,
                        "mutation_requested": False,
                        "note": "this invocation already took effect; no new mutation was issued",
                    }
                },
            )
            return env, 0
        rec_flight = store.find_in_flight(fingerprint)
        if rec_flight is not None:
            env = envelope(
                operation=op.name,
                status="partial",
                input=canonical,
                next=recovery_hint(rec_flight),
                run_id=rec_flight.run_id,
                job_id=rec_flight.job_id,
                error={
                    "code": "in_flight",
                    "message": "an identical invocation is recorded but never finished",
                    "details": {"phase": rec_flight.phase, "run_id": rec_flight.run_id},
                },
            )
            return env, 8

    run_id = new_run_id()
    # Derived, not random: the envelope must be a function of its inputs so a
    # workflow step can be diffed and hashed across runs. See runs.stable_job_id.
    job_id = stable_job_id(op.name, fingerprint)
    ctx = RunContext(ws.root, ws.artifacts_dir, run_id, job_id, op)
    # Resolve only the providers this operation declares. Resolving every
    # configured provider means one missing recording breaks unrelated
    # operations — a config problem should not become a workspace outage.
    needed = {ref.name for ref in op.credentials.values()}
    providers = {}
    for name, cfg in ws.providers.items():
        if name not in needed:
            continue
        try:
            providers[name] = resolve(cfg, ws.root)
        except ProviderError as exc:
            # A resolution failure is the operation's failure, so answer in the
            # same envelope shape as any other: an agent must not have to handle
            # two shapes for one class of error.
            return envelope(
                operation=op.name,
                status="failed",
                input=canonical,
                next={
                    "argv": _next_run_argv(op, format=fmt, idempotency=idem, approve=None),
                    "input": canonical,
                    "note": f"fix the {name!r} provider, then retry the same invocation",
                },
                error={
                    "code": "provider_error",
                    "message": exc.message,
                    "hint": exc.hint,
                    "details": {"provider": name, **(exc.details or {})},
                },
            ), 6
    for ref in op.credentials.values():
        if ref.name not in providers:
            env = envelope(
                operation=op.name,
                status="failed",
                input=canonical,
                next={"argv": _next_run_argv(op, format=fmt, idempotency=idem, approve=None), "input": canonical},
                error={
                    "code": "provider_not_configured",
                    "message": f"provider {ref.name!r} is not declared in scutch.toml",
                },
            )
            return env, 6

    # 4. plan ------------------------------------------------------------
    plan_obj: Plan | None = None
    if op.plan_fn is not None:
        try:
            plan_obj = _call(op.plan_fn, op, canonical, {"providers": providers, "ctx": ctx})
        except Exception as exc:  # noqa: BLE001 - provider/operation failure is data
            return _failure_envelope(op, canonical, idem, fmt, exc, run_id, job_id), 7
        if not isinstance(plan_obj, Plan):
            raise ContractError(
                f"{op.name}: plan function must return scutch.plan.Plan, got {type(plan_obj).__name__}"
            )
        plan_obj.digest = digest_for(op.name, canonical, conv.idempotency_value(), plan_obj)

    # 4a. nothing to do --------------------------------------------------
    if plan_obj is not None and plan_obj.is_empty():
        rec = RunRecord(
            run_id=run_id,
            job_id=job_id,
            operation=op.name,
            status="complete",
            input=canonical,
            fingerprint=fingerprint,
            idempotency=idem if op.idempotency_key else None,
            plan_digest=plan_obj.digest,
            phase="applied",
            created_at=time.time(),
            updated_at=time.time(),
        )
        result_payload, redacted = _safe_result(op, plan_obj)
        rec.envelope = envelope(
            operation=op.name,
            status="complete",
            input=canonical,
            plan=plan_obj.to_json(),
            result=result_payload,
            redacted=redacted,
            next={"argv": ["scutch", "status", job_id, "--format", fmt], "input": canonical},
            run_id=run_id,
            job_id=job_id,
        )
        store.save(rec)
        return rec.envelope, 0

    # 4b. approval required ----------------------------------------------
    if plan_obj is not None and op.requires_approval and approve_plan != plan_obj.digest:
        if approve_plan is not None:
            return _approval_mismatch(op, canonical, idem, fmt, plan_obj, approve_plan, run_id, job_id)
        rec = RunRecord(
            run_id=run_id,
            job_id=job_id,
            operation=op.name,
            status="needs_approval",
            input=canonical,
            fingerprint=fingerprint,
            idempotency=idem if op.idempotency_key else None,
            plan_digest=plan_obj.digest,
            phase="planned",
            created_at=time.time(),
            updated_at=time.time(),
        )
        env = envelope(
            operation=op.name,
            status="needs_approval",
            input=canonical,
            plan=plan_obj.to_json(),
            next={
                "argv": _next_run_argv(op, format=fmt, idempotency=idem, approve=plan_obj.digest),
                "input": canonical,
                "plan_digest": plan_obj.digest,
            },
            run_id=run_id,
            job_id=job_id,
            notes=["approval is bound to this digest; any change to inputs or plan yields a new one"],
        )
        rec.envelope = env
        store.save(rec)
        return env, 0

    # 5. apply -----------------------------------------------------------
    rec = RunRecord(
        run_id=run_id,
        job_id=job_id,
        operation=op.name,
        status="running",
        input=canonical,
        fingerprint=fingerprint,
        idempotency=idem if op.idempotency_key else None,
        plan_digest=plan_obj.digest if plan_obj else None,
        phase="applying",
        created_at=time.time(),
        updated_at=time.time(),
    )
    store.save(rec)
    started = time.time()
    try:
        raw_result = _call(op.fn, op, canonical, {"providers": providers, "ctx": ctx})
    except Exception as exc:  # noqa: BLE001
        rec.status = "failed"
        rec.phase = "failed"
        rec.error = _exc_json(exc)
        env = _failure_envelope(op, canonical, idem, fmt, exc, run_id, job_id)
        rec.envelope = env
        store.save(rec)
        return env, 7

    result_payload, redacted = to_public(raw_result)
    artifacts = _collect_artifacts(ctx)
    env = envelope(
        operation=op.name,
        status="complete",
        input=canonical,
        plan=plan_obj.to_json() if plan_obj else None,
        result=result_payload,
        redacted=redacted,
        artifacts=artifacts,
        next={
            "argv": ["scutch", "status", job_id, "--format", fmt],
            "input": canonical,
            "repeat": _next_run_argv(op, format=fmt, idempotency=idem, approve=None),
            "note": "to repeat deliberately, reuse this argv; the idempotency key prevents a second mutation",
        },
        run_id=run_id,
        job_id=job_id,
        extra={"timing": {"elapsed_ms": int((time.time() - started) * 1000)}},
    )
    rec.status = "complete"
    rec.phase = "applied"
    rec.artifacts = [a["path"] for a in artifacts]
    rec.envelope = env
    store.save(rec)
    superseded = store.supersede_planned(rec.plan_digest, rec.run_id)
    if superseded:
        env.setdefault("notes", []).append(
            "superseded_previews=" + ",".join(superseded)
        )
        rec.envelope = env
        store.save(rec)
    return env, 0


# --------------------------------------------------------------------------
# helpers


def _auto_key(op: Operation, canonical: dict[str, Any]) -> str:
    import hashlib

    canon = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return f"auto:{op.name}:{hashlib.sha256(canon.encode()).hexdigest()[:16]}"


def _exception_hint(exc: Exception) -> str | None:
    return getattr(exc, "hint", None)


def _exc_json(exc: Exception) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": getattr(exc, "code", "operation_failed"),
        "message": str(exc),
        "type": type(exc).__name__,
    }
    hint = _exception_hint(exc)
    if hint:
        payload["hint"] = hint
    details = getattr(exc, "details", None)
    if details:
        payload["details"] = details
    return payload


def _failure_envelope(op, canonical, idem, fmt, exc, run_id, job_id) -> dict[str, Any]:
    return envelope(
        operation=op.name,
        status="failed",
        input=canonical,
        next={
            "argv": _next_run_argv(op, format=fmt, idempotency=idem, approve=None),
            "input": canonical,
            "note": "fix the reported condition, then retry the same invocation",
        },
        error=_exc_json(exc),
        run_id=run_id,
        job_id=job_id,
        notes=["trace" in str(exc).lower() and "see run record" or "see run record"],
    )


def _approval_mismatch(op, canonical, idem, fmt, plan_obj, supplied, run_id, job_id):
    env = envelope(
        operation=op.name,
        status="needs_approval",
        input=canonical,
        plan=plan_obj.to_json(),
        next={
            "argv": _next_run_argv(op, format=fmt, idempotency=idem, approve=plan_obj.digest),
            "input": canonical,
            "plan_digest": plan_obj.digest,
        },
        error={
            "code": "approval_mismatch",
            "message": "approved digest does not match the current plan",
            "details": {"supplied": supplied, "current": plan_obj.digest},
            "hint": "nothing was applied; approve the digest returned here",
        },
        run_id=run_id,
        job_id=job_id,
    )
    return env, ApprovalError.exit_code


def _collect_artifacts(ctx: RunContext) -> list[dict[str, Any]]:
    out = []
    base = ctx.artifacts_dir / ctx.run_id
    if base.is_dir():
        for f in sorted(base.iterdir()):
            out.append(
                {
                    "name": f.stem,
                    "path": str(f.relative_to(ctx.workspace_root)),
                    "restricted": True,
                    "bytes": f.stat().st_size,
                }
            )
    return out


def _safe_result(op: Operation, plan_obj: Plan | None) -> tuple[Any, list[str]]:
    """Result for an empty plan: the plan's observation, or a schema-shaped stub.

    The operation's apply path is never entered, so no mutation can be issued
    on a no-change transition.
    """
    if plan_obj is not None and plan_obj.no_change_result is not None:
        payload, redacted = to_public(plan_obj.no_change_result)
        return payload, redacted
    stub = _stub_from_schema(op.output_schema())
    stub.setdefault("status", "no_change")
    return stub, []


def _stub_from_schema(schema: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, prop in (schema.get("properties") or {}).items():
        tp = prop.get("type")
        if tp == "array":
            out[name] = []
        elif tp == "object":
            out[name] = {}
        elif "enum" in prop:
            out[name] = prop["enum"][0]
        elif tp == "string":
            out[name] = ""
        elif tp == "integer":
            out[name] = 0
        elif tp == "boolean":
            out[name] = False
        elif tp == "number":
            out[name] = 0.0
        else:
            out[name] = None
    return out


def cancel(ws, job_or_run: str, fmt: str = "json") -> tuple[dict[str, Any], int]:
    """Cancel a pending plan. Only plans that have not applied anything.

    A cancelled plan can never be approved: its digest is dead. Nothing is
    "rolled back", because a `needs_approval` envelope has not mutated anything.
    """
    store = RunStore(ws.runs_dir)
    rec = store.find_job(job_or_run) or _load_or_none(store, job_or_run)
    if rec is None:
        raise NotFound(f"no run or job {job_or_run!r}", hint="run `scutch runs --format json`")

    if rec.phase == "applied" and rec.plan_digest:
        # A consequential run that already took effect: cancelling is too late,
        # and Scutch will not pretend otherwise.
        return envelope(
            operation=rec.operation,
            status="complete",
            input=rec.input,
            next={"argv": ["scutch", "status", rec.job_id, "--format", fmt], "input": rec.input},
            run_id=rec.run_id,
            job_id=rec.job_id,
            error={
                "code": "already_applied",
                "message": "the plan already took effect; it cannot be cancelled",
                "hint": "reverse it with the operation's inverse, if one exists",
            },
        ), 11
    if rec.phase not in ("planned",):
        return envelope(
            operation=rec.operation,
            status=rec.status,
            input=rec.input,
            next={"argv": ["scutch", "status", rec.job_id, "--format", fmt], "input": rec.input},
            run_id=rec.run_id,
            job_id=rec.job_id,
            error={
                "code": "not_cancellable",
                "message": f"run is {rec.phase!r}, which has no pending plan",
            },
        ), 12

    rec.status = "cancelled"
    rec.phase = "cancelled"
    rec.envelope = envelope(
        operation=rec.operation,
        status="cancelled",
        input=rec.input,
        plan=(rec.envelope or {}).get("plan"),
        next={
            "argv": ["scutch", "run", rec.operation, "--input", "@-", "--format", fmt],
            "input": rec.input,
            "note": "cancelled; nothing was applied. Run this to preview again.",
        },
        run_id=rec.run_id,
        job_id=rec.job_id,
        notes=["cancelled before approval; the plan digest is no longer valid"],
    )
    store.save(rec)
    return rec.envelope, 0


def _load_or_none(store: RunStore, run_id: str):
    try:
        return store.load(run_id)
    except FileNotFoundError:
        return None


def status(ws, job_or_run: str, fmt: str = "json") -> tuple[dict[str, Any], int]:
    store = RunStore(ws.runs_dir)
    rec = store.find_job(job_or_run)
    if rec is None:
        try:
            rec = store.load(job_or_run)
        except FileNotFoundError:
            raise NotFound(f"no run or job {job_or_run!r}", hint="run `scutch runs --format json`")
    body = {
        "kind": "scutch.status/v1",
        "job_id": rec.job_id,
        "run_id": rec.run_id,
        "operation": rec.operation,
        "status": rec.status,
        "phase": rec.phase,
        "input": rec.input,
        "plan_digest": rec.plan_digest,
        "artifacts": rec.artifacts,
        "created_at": rec.to_json()["created_at_iso"],
        "updated_at": rec.to_json()["updated_at_iso"],
        "result": (rec.envelope or {}).get("result"),
        "error": rec.error,
    }
    if rec.phase in ("applying", "starting"):
        body["next"] = recovery_hint(rec)
    elif rec.phase == "superseded":
        body["superseded_by"] = rec.superseded_by
        body["next"] = {
            "argv": ["scutch", "status", rec.superseded_by, "--format", fmt],
            "input": rec.input,
            "note": "this preview was applied; inspect the applied run instead",
        }
    elif rec.phase == "cancelled":
        body["next"] = {
            "argv": ["scutch", "run", rec.operation, "--input", "@-", "--format", fmt],
            "input": rec.input,
            "note": "this plan was cancelled; nothing was applied",
        }
    elif rec.phase == "planned":
        body["next"] = {
            "argv": ["scutch", "run", rec.operation, "--input", "@-", "--format", fmt,
                     "--approve-plan", rec.plan_digest],
            "input": rec.input,
            "plan_digest": rec.plan_digest,
            "note": "this plan is still pending; approving it applies exactly these changes",
            "cancel": ["scutch", "cancel", rec.job_id, "--format", fmt],
        }
    else:
        body["next"] = {
            "argv": ["scutch", "run", rec.operation, "--input", "@-", "--format", fmt],
            "input": rec.input,
        }
    return body, 0
