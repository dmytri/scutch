"""Read paths: discovery, description, examples, context, doctor.

All of these are pure projections of the registry — the same data the TUI
renders and the same schemas ``run`` enforces.
"""

from __future__ import annotations

import json
import re
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
        "spec": (str(spec.relative_to(ws.root)) if (spec := spec_for(ws, op)) else None),
    }


def spec_root(ws) -> Path:
    """Where features live, per the workspace's declared conventions."""
    return ws.root / ws.conventions.features_root


def spec_for(ws, op) -> Path | None:
    """The Gherkin feature that specifies an operation.

    Either ``specs/<name>.feature``, or any feature that names the operation —
    one feature may cover a family of related reads.
    """
    root = spec_root(ws)
    if not root.is_dir():
        return None
    # 1. a feature named after the operation, at any depth
    for path in sorted(root.rglob(f"{op.name}.feature")):
        return path
    # 2. a file whose name is the operation's last segment (customer_repair)
    leaf = op.name.split(".")[-1]
    for path in sorted(root.rglob(f"{leaf}.feature")):
        return path
    # 3. a feature that declares it covers this operation
    for path in sorted(root.rglob("*.feature")):
        text = path.read_text()
        covers = [line for line in text.splitlines() if line.strip().lower().startswith("covers:")]
        if any(op.name in line for line in covers):
            return path
    # 4. last resort: any feature that names it
    for path in sorted(root.rglob("*.feature")):
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

    # Declared conventions, each a failing check rather than a paragraph.
    check_conventions(ws, check)

    # Reproducibility: a step's output must be diffable. Check only operations
    # with a declared example so the doctor stays fast and needs no fixtures.
    from .envelope import digest as _digest  # noqa: F401  (documented contract)
    from .runner import execute as _execute

    drift = []
    for op in ws.registry.all():
        if not op.examples:
            continue
        sample = dict(op.examples[0].get("input", {}))
        try:
            first, _ = _execute(ws, op.name, sample)
            second, _ = _execute(ws, op.name, sample)
        except Exception as exc:  # noqa: BLE001 - doctor reports, never raises
            drift.append({"operation": op.name, "error": str(exc)[:160]})
            continue
        from .envelope import canonical as _canon

        if _canon(first) != _canon(second):
            drift.append({"operation": op.name, "error": "envelope differs between identical runs"})
    check(
        "reproducible",
        not drift,
        drift or "every example reproduces its envelope",
    )
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


# --------------------------------------------------------------------------
# Conventions
#
# Conventions an agent cannot be *told* are conventions it will not follow. Each
# one below is a check with a failing workspace, so the loop is: run doctor, read
# the failing check, fix it. The declarations live in scutch.toml so a workspace
# states its own conventions rather than inheriting Scutch's opinions.

def conventions(ws) -> Any:
    """The workspace's declared conventions (see workspace.Conventions)."""
    return ws.conventions


SECRET_PATTERNS = [
    re.compile(r"""(?i)\b(api[_-]?key|token|secret|password)\s*[:=]\s*["'][^"'{][^"']{7,}["']"""),
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bya29\.[A-Za-z0-9._-]{20,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]{20,}\b"),
]


def _sql_siblings(ws, op) -> list[Path]:
    """The `.sql` file an operation declares, or one named after it.

    An operation that computes over data names its query with
    ``@operation(query="operations/x/y.sql")``. The file is then a standalone
    artifact — runnable in `duckdb` by hand — and the operation is the guardrail
    around it.
    """
    declared = getattr(op, "query", None)
    if declared:
        path = ws.root / declared
        return [path] if path.is_file() else []
    ops_dir = ws.root / "operations"
    if not ops_dir.is_dir():
        return []
    mod = op.module.split(".")[-1]
    stem = op.name.split(".")[-1]
    found: set[Path] = set()
    for pattern in (f"{mod}.sql", f"{stem}.sql"):
        found.update(ops_dir.rglob(pattern))
    return sorted(found)


def check_conventions(ws, check) -> None:
    """Enforce every declared convention. ``check`` is doctor's recorder.

    Each violation is a failing check, not a paragraph in a document: an agent
    that never reads the conventions still cannot pass without following them.
    """
    conv = ws.conventions

    if conv.queries == "duckdb":
        # An operation that claims to transform data must point at its .sql.
        # `query=` is the declaration; the file must exist.
        no_sql = [
            op.name
            for op in ws.registry.all()
            if op.query and not (ws.root / op.query).is_file()
        ]
        check(
            "queries_are_sql",
            not no_sql,
            no_sql or "every declared query file exists",
        )

    if conv.steps == "poe":
        shell_steps: list[str] = []
        for path in sorted((ws.root / "workflows").glob("*.y*ml")):
            if "command/shell" in path.read_text():
                shell_steps.append(path.name)
        check(
            "steps_are_poe",
            not shell_steps,
            shell_steps or "no command/shell steps",
        )

    if conv.secrets == "provider":
        hits: list[dict[str, str]] = []
        ops_dir = ws.root / "operations"
        for file in (ops_dir.rglob("*.py") if ops_dir.is_dir() else []):
            text = file.read_text()
            for rx in SECRET_PATTERNS:
                for m in rx.finditer(text):
                    hits.append(
                        {"file": str(file.relative_to(ws.root)), "match": m.group(0)[:24] + "…"}
                    )
        cfg = ws.root / "scutch.toml"
        if cfg.is_file():
            for rx in SECRET_PATTERNS:
                for m in rx.finditer(cfg.read_text()):
                    hits.append({"file": "scutch.toml", "match": m.group(0)[:24] + "…"})
        check("no_inline_secrets", not hits, hits or "no credential-shaped literals found")

    if conv.feature_is_workflow:
        specs_dir = ws.root / conv.features_root
        features = sorted(specs_dir.rglob("*.feature")) if specs_dir.is_dir() else []
        unclaimed = [o.name for o in ws.registry.all() if spec_for(ws, o) is None]
        flat = [str(f.relative_to(specs_dir)) for f in features if f.parent == specs_dir]
        check(
            "features_are_workflows" if flat else "features_bound",
            not unclaimed and not flat,
            {
                "unclaimed_operations": unclaimed,
                "features_outside_a_domain_folder": flat,
                "features": len(features),
            }
            if (unclaimed or flat)
            else f"{len(features)} feature(s), every operation claimed",
        )
