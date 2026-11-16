"""Read paths: discovery, description, examples, context, doctor.

All of these are pure projections of the registry — the same data the TUI
renders and the same schemas ``run`` enforces.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .contract import Operation
from .errors import NotFound


def list_ops(ws, *, effect: str | None = None, json_mode: bool = True) -> dict[str, Any]:
    ops = ws.registry.all()
    if effect:
        ops = [o for o in ops if o.effect == effect]
    return {
        "kind": "scutch.list/v1",
        "workspace": str(ws.root),
        "operations": [
            {
                "name": o.name,
                "version": o.version,
                "summary": o.summary,
                "effect": o.effect,
                "approval": o.approval,
                "parameters": [
                    {"name": p.name, "required": p.default is p.empty} for p in o.params
                ],
                "deprecated": o.deprecated,
            }
            for o in ops
        ],
    }


def describe(ws, name: str) -> dict[str, Any]:
    op = ws.registry.get(name)
    return op.describe()


def examples(ws, name: str) -> dict[str, Any]:
    op = ws.registry.get(name)
    return {
        "kind": "scutch.examples/v1",
        "operation": op.name,
        "examples": [
            {
                "name": ex.get("name", "example"),
                "why": ex.get("why", ""),
                "input": ex.get("input", {}),
                "argv": ["scutch", "run", op.name, "--input", "@-", "--format", "json"],
                "expect": ex.get("expect"),
            }
            for ex in op.examples
        ],
        "spec": f"{op.name} behaviour is specified in specs/{op.name}.feature",
    }


def spec_for(ws, op) -> Path | None:
    """The Gherkin feature that specifies an operation.

    Either ``specs/<name>.feature``, or any feature that names the operation —
    one feature may cover a family of related reads.
    """
    exact = ws.root / "specs" / f"{op.name}.feature"
    if exact.is_file():
        return exact
    for path in sorted((ws.root / "specs").glob("*.feature")):
        if op.name in path.read_text():
            return path
    return None


def doctor(ws) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    optional: list[str] = []

    def check(name: str, ok: bool, detail: Any = None, required: bool = True) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail, "required": required})
        if not required:
            optional.append(name)

    try:
        import jsonschema  # noqa: F401

        check("jsonschema", True, jsonschema.__version__)
    except Exception as exc:  # noqa: BLE001
        check("jsonschema", False, str(exc))

    try:
        from .tui import available

        check("tui", available())
    except Exception as exc:  # noqa: BLE001
        check("tui", False, str(exc))

    check(
        "just",
        shutil.which("just") is not None,
        shutil.which("just") or "optional alias layer; `just` recipes are not required",
        required=False,
    )

    invalid = {n: probs for n, probs in ws.registry.validate_all().items() if probs}
    check("contracts", not invalid, invalid or f"{len(ws.registry.all())} operations valid")

    missing_plan = [o.name for o in ws.registry.all() if o.effect == "plan_apply" and o.plan_fn is None]
    check("plan_apply_has_plan", not missing_plan, missing_plan or "all plan/apply ops declare a plan")

    no_spec = [o.name for o in ws.registry.all() if spec_for(ws, o) is None]
    check("specs_present", not no_spec, no_spec or "every operation has a Gherkin spec")

    prov = []
    for name, cfg in ws.providers.items():
        if cfg.mode == "replay":
            # Replay needs recorded exchanges, not a credential.
            data_dir = ws.root / (cfg.data or f"fixtures/{name}")
            cassettes = sorted(data_dir.glob("*.json")) if data_dir.is_dir() else []
            prov.append(
                {
                    "provider": name,
                    "mode": "replay",
                    "ok": bool(cassettes),
                    "cassettes": len(cassettes),
                }
            )
        else:
            import os

            ok = bool(cfg.env and os.environ.get(cfg.env))
            prov.append({"provider": name, "mode": "live", "ok": ok, "env": cfg.env})
    check("providers", all(p["ok"] for p in prov) if prov else True, prov)

    check("runs_dir_writable", _writable(ws.runs_dir))
    check("artifacts_dir_writable", _writable(ws.artifacts_dir))

    required_ok = all(c["ok"] for c in checks if c.get("required", True))
    return {
        "kind": "scutch.doctor/v1",
        "ok": required_ok,
        "checks": checks,
        "optional_failures": [c["check"] for c in checks if not c["ok"] and not c.get("required", True)],
    }


def _type_of(schema: dict[str, Any]) -> str:
    if "enum" in schema:
        return "enum(" + ", ".join(str(v) for v in schema["enum"]) + ")"
    if "anyOf" in schema:
        return " | ".join(_type_of(s) for s in schema["anyOf"])
    tp = schema.get("type", "any")
    if tp == "array":
        return f"{_type_of(schema.get('items', {}))}[]"
    return tp


def _writable(p: Path) -> bool:
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".write-probe"
        probe.write_text("ok")
        probe.unlink()
        return True
    except OSError:
        return False


def render_docs(ws, out_dir: Path | None = None) -> list[Path]:
    """Generate markdown from the same descriptors. No separate doc source."""
    out_dir = out_dir or ws.root / "docs" / "generated"
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    index = ["# Operations", "", ws.context()["bootstrap"], "", "| operation | effect | approval | summary |", "|---|---|---|---|"]
    for op in ws.registry.all():
        index.append(f"| [`{op.name}`]({op.name}.md) | {op.effect} | {op.approval} | {op.summary} |")
    index += ["", "Generated by `scutch docs`. Edit the operation, not this file.", ""]
    p = out_dir / "index.md"
    p.write_text("\n".join(index))
    written.append(p)

    for op in ws.registry.all():
        d = op.describe()
        lines = [f"# {op.name}", "", op.summary, "", f"- **Effect:** {op.effect}", f"- **Approval:** {op.approval}"]
        if op.credentials:
            lines.append("- **Providers:** " + ", ".join(f"`{v.name}`" for v in op.credentials.values()))
        if op.idempotency_key:
            lines.append("- **Idempotency key:** " + ", ".join(f"`{k}`" for k in op.idempotency_key))
        lines += ["", "## Parameters", "", "| name | type | required | default |", "|---|---|---|---|"]
        for prm in d["parameters"]:
            default = json.dumps(prm.get("default")) if "default" in prm else "—"
            lines.append(
                f"| `{prm['name']}` | `{_type_of(prm['schema'])}` | {prm['required']} | {default} |"
            )
        lines += ["", "## Invocation", "", "```bash", " ".join(d["invocation"]["argv"]), "```", ""]
        lines += ["## Behaviour", "", "```gherkin"]
        spec = spec_for(ws, op)
        lines.append(spec.read_text().strip() if spec else "No spec yet.")
        lines += ["```", "", "## Source", "", f"`{op.implementation}`", ""]
        p = out_dir / f"{op.name}.md"
        p.write_text("\n".join(lines))
        written.append(p)
    return written
