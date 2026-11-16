"""Command line interface.

Contract of the JSON surface: stdout is clean JSON, diagnostics go to stderr,
and non-interactive execution never blocks on a prompt. Every command that
can continue returns a complete ``next`` block.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer

from . import discovery
from .envelope import dumps
from .errors import ScutchError, UsageError
from .plan import Plan  # noqa: F401  (re-exported for operation modules)
from .runner import execute
from .runner import status as runner_status
from .workspace import load_workspace

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Scutch — typed operations for humans and agents.")


# --------------------------------------------------------------------------
# helpers


def _emit(payload: Any, fmt: str) -> None:
    if fmt == "json":
        sys.stdout.write(dumps(payload) + "\n")
    elif fmt == "text":
        sys.stdout.write(_text(payload) + "\n")
    else:
        raise UsageError(f"unknown format {fmt!r}", hint="use --format json|text")


def _text(payload: dict[str, Any]) -> str:
    kind = payload.get("kind", "")
    if kind.startswith("scutch.envelope"):
        lines = [f"{payload['operation']}  [{payload['status']}]"]
        if payload.get("plan"):
            lines.append("plan:")
            lines += ["  " + l for l in _plan_lines(payload["plan"])]
        if payload.get("result") is not None:
            lines.append("result: " + json.dumps(payload["result"], indent=2).replace("\n", "\n  "))
        if payload.get("error"):
            err = payload["error"]
            lines.append(f"error: {err.get('code')}: {err.get('message')}")
            if err.get("hint"):
                lines.append(f"hint:  {err['hint']}")
        if payload.get("next", {}).get("argv"):
            lines.append("next:")
            lines.append("  " + " ".join(payload["next"]["argv"]))
            lines.append("  input: " + json.dumps(payload["next"].get("input"), sort_keys=True))
        if payload.get("notes"):
            lines += ["note: " + n for n in payload["notes"]]
        return "\n".join(lines)
    return json.dumps(payload, indent=2)


def _plan_lines(plan: dict[str, Any]) -> list[str]:
    out = []
    for c in plan.get("changes", []):
        target = f" {c['target']}" if c.get("target") else ""
        out.append(f"+ {c['kind']}{target} = {json.dumps(c.get('value'), sort_keys=True)}")
    if plan.get("digest"):
        out.append(f"digest {plan['digest']}")
    return out


def _read_input(path: str | None) -> dict[str, Any]:
    if path in (None, "-", "@-"):
        data = sys.stdin.read()
        if not data.strip():
            return {}
    elif path.lstrip().startswith("{"):
        data = path
    else:
        p = Path(path[1:] if path.startswith("@") else path)
        if not p.is_file():
            raise UsageError(f"input file not found: {p}")
        data = p.read_text()
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError as exc:
        raise UsageError(f"input is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise UsageError("input must be a JSON object")
    return parsed


def _fail(exc: ScutchError) -> None:
    sys.stderr.write(json.dumps({"error": exc.to_json()}) + "\n")
    raise typer.Exit(exc.exit_code)


_WS: list[str | None] = [None]


def _ws():
    try:
        return load_workspace(_WS[0] or Path.cwd())
    except ScutchError as exc:
        _fail(exc)


@app.callback()
def main(
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace root (default: cwd)"),
) -> None:
    _WS[0] = workspace


# --------------------------------------------------------------------------
# discovery


@app.command("context")
def context_cmd(format: str = typer.Option("json", "--format", "-f")) -> None:
    """Workspace policies, providers and the bounded operation catalogue."""
    _emit(_ws().context(), format)


@app.command("list")
def list_cmd(
    effect: str | None = typer.Option(None, "--effect", help="read | plan_apply | mutate"),
    format: str = typer.Option("json", "--format", "-f"),
) -> None:
    """Available operations."""
    _emit(discovery.list_ops(_ws(), effect=effect), format)


@app.command("describe")
def describe_cmd(
    operation: str = typer.Argument(..., help="Operation name (unique prefix accepted)"),
    format: str = typer.Option("json", "--format", "-f"),
) -> None:
    """Everything needed to invoke an operation correctly."""
    try:
        _emit(discovery.describe(_ws(), operation), format)
    except ScutchError as exc:
        _fail(exc)


@app.command("examples")
def examples_cmd(
    operation: str = typer.Argument(...),
    format: str = typer.Option("json", "--format", "-f"),
) -> None:
    """Executable examples for an operation."""
    try:
        _emit(discovery.examples(_ws(), operation), format)
    except ScutchError as exc:
        _fail(exc)


@app.command("doctor")
def doctor_cmd(format: str = typer.Option("json", "--format", "-f")) -> None:
    """Check the workspace can serve every documented capability."""
    _emit(discovery.doctor(_ws()), format)


@app.command("docs")
def docs_cmd(
    out: str | None = typer.Option(None, "--out", help="Output directory (default docs/generated)"),
    format: str = typer.Option("json", "--format", "-f"),
) -> None:
    """Regenerate documentation from the descriptors."""
    ws = _ws()
    written = discovery.render_docs(ws, Path(out) if out else None)
    _emit({"kind": "scutch.docs/v1", "written": [str(p.relative_to(ws.root)) for p in written]}, format)


# --------------------------------------------------------------------------
# execution


@app.command("run")
def run_cmd(
    operation: str = typer.Argument(...),
    input: str | None = typer.Option(
        None, "--input", "-i", help="JSON literal, @file, file path, or - for stdin"
    ),
    approve_plan: str | None = typer.Option(None, "--approve-plan", help="Digest from a needs_approval envelope"),
    idempotency_key: str | None = typer.Option(None, "--idempotency-key"),
    format: str = typer.Option("json", "--format", "-f"),
) -> None:
    """Perform one lifecycle transition and return an envelope."""
    ws = _ws()
    try:
        payload = _read_input(input)
    except ScutchError as exc:
        _fail(exc)
    try:
        env, code = execute(
            ws,
            operation,
            payload,
            approve_plan=approve_plan,
            idempotency_key=idempotency_key,
            fmt=format,
        )
    except ScutchError as exc:
        _fail(exc)
    _emit(env, format)
    raise typer.Exit(code)


@app.command("status")
def status_cmd(
    job_or_run: str = typer.Argument(...),
    format: str = typer.Option("json", "--format", "-f"),
) -> None:
    """Inspect a recorded run by job or run id."""
    try:
        payload, code = runner_status(_ws(), job_or_run, format)
    except ScutchError as exc:
        _fail(exc)
    _emit(payload, format)
    raise typer.Exit(code)


@app.command("runs")
def runs_cmd(
    limit: int = typer.Option(20, "--limit", "-n"),
    format: str = typer.Option("json", "--format", "-f"),
) -> None:
    """Recent local run records (newest first)."""
    from .runs import RunStore

    store = RunStore(_ws().runs_dir)
    recs = store.recent(limit=limit)
    _emit(
        {
            "kind": "scutch.runs/v1",
            "runs": [
                {
                    "run_id": r.run_id,
                    "job_id": r.job_id,
                    "operation": r.operation,
                    "status": r.status,
                    "phase": r.phase,
                    "input": r.input,
                    "artifacts": r.artifacts,
                }
                for r in recs
            ],
        },
        format,
    )


@app.command("recover")
def recover_cmd(
    run_id: str = typer.Argument(...),
    format: str = typer.Option("json", "--format", "-f"),
) -> None:
    """Report what an interrupted run needs, without repeating the effect."""
    from .runs import RunStore, recovery_hint

    store = RunStore(_ws().runs_dir)
    try:
        rec = store.load(run_id)
    except FileNotFoundError:
        _fail(UsageError(f"no run {run_id!r}"))
    payload = {
        "kind": "scutch.recover/v1",
        "run_id": rec.run_id,
        "job_id": rec.job_id,
        "operation": rec.operation,
        "phase": rec.phase,
        "status": rec.status,
        "input": rec.input,
        "next": recovery_hint(rec),
    }
    _emit(payload, format)


# --------------------------------------------------------------------------
# human interface


@app.command("ui")
def ui_cmd() -> None:
    """Open the terminal interface (same contracts, same transitions)."""
    from .tui import run_app

    run_app(_ws())


@app.command("validate")
def validate_cmd(format: str = typer.Option("json", "--format", "-f")) -> None:
    """Prove implementation, schemas, specs and examples agree."""
    ws = _ws()
    problems = {n: p for n, p in ws.registry.validate_all().items() if p}
    spec_problems: dict[str, list[str]] = {}
    for op in ws.registry.all():
        if discovery.spec_for(ws, op) is None:
            spec_problems[op.name] = ["missing spec"]
    ok = not problems and not spec_problems
    _emit(
        {
            "kind": "scutch.validate/v1",
            "ok": ok,
            "operations": len(ws.registry.all()),
            "contract_problems": problems,
            "spec_problems": spec_problems,
        },
        format,
    )
    raise typer.Exit(0 if ok else 1)


def _entry() -> None:
    """Console-script entry point. ``main`` is the Typer *callback*, not the app."""
    app()


if __name__ == "__main__":
    app()
