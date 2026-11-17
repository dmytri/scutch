"""Human-facing language for the TUI.

An agent reads an envelope: `status`, `next.argv`, error codes. A person needs
sentences: what this will do, whether it is safe, what went wrong, what to do
next. Those are projections of the same data — one vocabulary, two renderings —
so this module is pure and testable without a terminal.

Nothing here invents facts. Every sentence is derived from the contract or the
envelope; if the data does not support a sentence, we say less rather than guess.
"""

from __future__ import annotations

from typing import Any

#: Envelope status -> what a person calls it.
STATUS_LABEL: dict[str, str] = {
    "needs_input": "A few details needed",
    "needs_approval": "Ready — please review",
    "running": "Working…",
    "partial": "Stopped part-way",
    "complete": "Done",
    "failed": "That didn't work",
    "cancelled": "Cancelled",
}

#: Effect -> how a person should read it.
EFFECT_LABEL: dict[str, str] = {
    "read": "Reads only — changes nothing",
    "plan_apply": "Will change something, with your approval",
    "mutate": "Will change something, with your approval",
}

#: Error code -> a sentence and the next step.
ERROR_GUIDE: dict[str, tuple[str, str]] = {
    "missing_input": ("Some details are still missing.", "Fill them in and continue."),
    "invalid_input": ("A value isn't allowed.", "Pick from the permitted values shown."),
    "approval_mismatch": (
        "The thing you approved has changed since you looked at it.",
        "Review the new summary, then approve again. Nothing was changed.",
    ),
    "plan_cancelled": ("You cancelled this earlier.", "Start again if you still want it."),
    "idempotency_conflict": (
        "This was already run with different details.",
        "Start a new attempt if this is a different task.",
    ),
    "in_flight": (
        "An earlier attempt at this never finished.",
        "Check the run before trying again — it may already have happened.",
    ),
    "provider_not_configured": (
        "A connection this needs isn't set up.",
        "Ask whoever maintains this repository to configure it.",
    ),
    "provider_error": ("The external service didn't answer as expected.", "Details are below."),
    "not_found": ("That item doesn't exist.", "Pick one from the list."),
    "operation_failed": ("The operation reported a problem.", "Details are below."),
    "plan_only": ("Nothing to do — it's already in that state.", "No action needed."),
}


def status_label(status: str) -> str:
    return STATUS_LABEL.get(status, status.replace("_", " ").title())


def effect_label(effect: str) -> str:
    return EFFECT_LABEL.get(effect, effect)


def error_guide(error: dict[str, Any] | None) -> tuple[str, str]:
    """(what happened, what to do) for an error, falling back to its own message."""
    if not error:
        return ("", "")
    code = str(error.get("code", ""))
    if code in ERROR_GUIDE:
        return ERROR_GUIDE[code]
    message = str(error.get("message") or "").strip()
    return (message or "Something went wrong.", str(error.get("hint") or "").strip())


def describe_operation(op) -> dict[str, Any]:
    """A card a person can read: what it does, whether it's safe, what it needs.

    Derived from the contract, so the TUI cannot describe an operation
    differently from how it behaves.
    """
    d = op.describe()
    params = d["parameters"]
    required = [p for p in params if p["required"]]
    optional = [p for p in params if not p["required"]]
    return {
        "name": op.name,
        "title": op.summary,
        "effect": effect_label(op.effect),
        "safe": op.effect == "read",
        "needs_approval": op.approval in ("auto", "required") and op.effect != "read",
        "required": required,
        "optional": optional,
        "about": (op.description or "").strip(),
        "providers": [v.name for v in op.credentials.values()],
        "examples": d.get("examples", []),
    }


def field_help(name: str, schema: dict[str, Any]) -> str:
    """A one-line hint under a form field: what to type, and what's allowed."""
    if "enum" in schema:
        return "Choose one of: " + ", ".join(str(v) for v in schema["enum"])
    bits: list[str] = []
    if schema.get("description"):
        bits.append(str(schema["description"]))
    if schema.get("pattern"):
        bits.append(f"format {schema['pattern']}")
    if schema.get("type") == "string" and not bits:
        bits.append("text")
    if "examples" in schema:
        bits.append("e.g. " + ", ".join(str(e) for e in schema["examples"][:2]))
    return " · ".join(bits)


def plan_sentences(plan: dict[str, Any] | None) -> list[str]:
    """What the plan will do, in plain sentences.

    Change kinds are operation-defined strings (`permission.add`), so this reads
    them rather than claiming to understand every possible one: a kind it has
    not seen is shown as stated, not guessed at.
    """
    if not plan:
        return []
    changes = plan.get("changes") or []
    if not changes:
        return ["Nothing will change — it is already in that state."]

    out: list[str] = []
    for change in changes:
        kind = str(change.get("kind", "change"))
        target = change.get("target")
        value = change.get("value")
        verb = {
            "permission.add": "Add the permission",
            "clear": "Clear",
            "insert": "Add rows to",
            "update": "Update",
            "delete": "Delete from",
        }.get(kind)
        if verb and value is not None and kind == "permission.add":
            out.append(f"{verb} “{value}”" + (f" for {target}" if target else "") + ".")
        elif verb and target:
            out.append(f"{verb} {target}" + (f" ({value})" if value is not None else "") + ".")
        elif target:
            out.append(f"{kind}: {target} = {value!r}.")
        else:
            out.append(f"{kind}: {value!r}.")
    return out


def result_rows(result: Any) -> list[tuple[str, str]]:
    """A result as label/value pairs a person can scan."""
    if result is None:
        return []
    if isinstance(result, dict):
        rows = []
        for key, value in result.items():
            label = str(key).replace("_", " ").strip().capitalize()
            if isinstance(value, list):
                rows.append((label, f"{len(value)} item(s)"))
            elif isinstance(value, dict):
                rows.append((label, ", ".join(f"{k}={v}" for k, v in value.items())))
            else:
                rows.append((label, str(value)))
        return rows
    if isinstance(result, list):
        return [(f"item {i + 1}", str(v)) for i, v in enumerate(result)]
    return [("result", str(result))]


def outcome(env: dict[str, Any]) -> dict[str, Any]:
    """Everything a result screen needs, from one envelope."""
    status = str(env.get("status", ""))
    happened, what_to_do = error_guide(env.get("error"))
    return {
        "status": status,
        "label": status_label(status),
        "ok": status in ("complete",),
        "happened": happened,
        "what_to_do": what_to_do,
        "plan": plan_sentences(env.get("plan")),
        "rows": result_rows(env.get("result")),
        "redacted": env.get("redacted") or [],
        "artifacts": [a.get("path") for a in (env.get("artifacts") or [])],
        "notes": env.get("notes") or [],
        "job_id": env.get("job_id"),
    }


def workflow_summary(ws, path) -> dict[str, Any]:
    """A feature as a person reads it: what it does, and its steps in order."""
    from .gherkin import parse_feature

    scenarios = parse_feature(path)
    return {
        "path": str(path.relative_to(ws.root)) if path.is_relative_to(ws.root) else str(path),
        "name": scenarios[0].feature if scenarios else path.stem,
        "steps": [s.name for s in scenarios],
        "count": len(scenarios),
    }
