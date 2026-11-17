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
from .runner import cancel as runner_cancel
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


@app.command("doctor")
def doctor_cmd(format: str = typer.Option("json", "--format", "-f")) -> None:
    """Check the workspace can serve every documented capability."""
    _emit(discovery.doctor(_ws()), format)


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
    job_or_run: str | None = typer.Argument(None, help="Job or run id; omit for recent runs"),
    limit: int = typer.Option(20, "--limit", "-n"),
    format: str = typer.Option("json", "--format", "-f"),
) -> None:
    """Inspect a recorded run, or list recent ones."""
    ws = _ws()
    if job_or_run is None:
        from .runs import RunStore

        recs = RunStore(ws.runs_dir).recent(limit=limit)
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
                    }
                    for r in recs
                ],
            },
            format,
        )
        return
    try:
        payload, code = runner_status(ws, job_or_run, format)
    except ScutchError as exc:
        _fail(exc)
    _emit(payload, format)
    raise typer.Exit(code)


@app.command("ui")
def ui_cmd(
    run: str | None = typer.Argument(None, help="Open straight into an operation or feature"),
    operation: str | None = typer.Option(None, "--operation", "-o"),
    feature: str | None = typer.Option(None, "--workflow", "-w"),
) -> None:
    """Open the guided terminal interface.

    The human side of the same lifecycle: pick an operation, fill a form, read a
    plain-language summary, approve, see labelled results. Approving here is
    exactly `--approve-plan <digest>`, and every screen shows the command that
    reproduces it.
    """
    from . import tui

    ws = _ws()
    if not tui.available():
        _fail(UsageError(
            "the terminal interface needs textual",
            hint="textual is a declared dependency; reinstall scutch",
        ))
    from .workspace import resolve_feature

    target_op = operation
    target_feature = None
    reference = run or feature
    if reference:
        try:
            ws.registry.get(reference)
            target_op = reference
        except ScutchError:
            try:
                target_feature = resolve_feature(ws, reference)
            except ScutchError as exc:
                _fail(exc)
    tui.run_ui(ws, feature=target_feature, operation=target_op)


@app.command("cancel")
def cancel_cmd(
    job_or_run: str = typer.Argument(..., help="Job or run id from a needs_approval envelope"),
    format: str = typer.Option("json", "--format", "-f"),
) -> None:
    """Cancel a pending plan. Its digest becomes unusable; nothing was applied."""
    try:
        payload, code = runner_cancel(_ws(), job_or_run, format)
    except ScutchError as exc:
        _fail(exc)
    _emit(payload, format)
    raise typer.Exit(code)


@app.command("new")
def new_cmd(
    kind: str = typer.Argument(..., help="operation | feature"),
    name: str = typer.Argument(..., help="Dotted name, e.g. saleor.grist.master.write"),
    effect: str = typer.Option("read", "--effect", help="read | plan_apply | mutate"),
    query: bool = typer.Option(False, "--query", help="This operation computes over data: scaffold a .sql file"),
    domain: str | None = typer.Option(None, "--domain", help="Feature folder (defaults to the name's first segment)"),
    format: str = typer.Option("json", "--format", "-f"),
) -> None:
    """Scaffold a compliant operation or feature.

    The point is that the *shape* is decided here, not by the author: the
    decorator, the query file, the plan stub and the feature skeleton all come
    from the conventions declared in scutch.toml. An agent fills in bodies; it
    does not have to remember the rules, and `doctor` will catch it if it
    deviates.
    """
    from .scaffold import scaffold_feature, scaffold_operation

    ws = _ws()
    try:
        if kind == "operation":
            written = scaffold_operation(ws, name, effect=effect, query=query)
        elif kind == "feature":
            written = scaffold_feature(ws, name, domain=domain)
        else:
            raise UsageError(f"unknown kind {kind!r}", hint="use `operation` or `feature`")
    except ScutchError as exc:
        _fail(exc)
    _emit(
        {
            "kind": "scutch.new/v1",
            "scaffolded": kind,
            "name": name,
            "written": [str(p.relative_to(ws.root)) for p in written],
            "next": [
                "fill in the bodies",
                "scutch doctor",
                f"scutch workflow {domain or name.split('.')[0]}",
            ],
        },
        format,
    )


@app.command("workflow")
def workflow_cmd(
    feature: str = typer.Argument(..., help="Feature file or a name within specs/"),
    list_only: bool = typer.Option(False, "--list", help="Show the steps without running them"),
    keep_going: bool = typer.Option(False, "--keep-going", help="Diagnose a suite; a real run stops"),
    format: str = typer.Option("json", "--format", "-f"),
) -> None:
    """Run a feature as a workflow: scenarios in order, stop on failure."""
    from . import gherkin as g
    from .harness import LocalHarness
    from .steps import LIBRARY, Steps

    from .workspace import resolve_feature

    ws = _ws()
    try:
        candidate = resolve_feature(ws, feature)
    except ScutchError as exc:
        _fail(exc)

    scenarios = g.parse_feature(candidate)
    if list_only:
        _emit(
            {
                "kind": "scutch.workflow/v1",
                "path": str(candidate.resolve().relative_to(ws.root))
                if candidate.resolve().is_relative_to(ws.root)
                else str(candidate),
                "scenarios": [{"name": s.name, "steps": len(s.steps)} for s in scenarios],
                "count": len(scenarios),
            },
            format,
        )
        return

    harness = LocalHarness(ws)
    run = g.run_feature(scenarios, Steps(harness), LIBRARY, keep_going=keep_going)
    resolved = candidate.resolve()
    display = str(resolved.relative_to(ws.root)) if resolved.is_relative_to(ws.root) else str(resolved)
    payload = run.to_json() | {"kind": "scutch.workflow/v1", "path": display}
    _emit(payload, format)
    raise typer.Exit(0 if run.ok else 1)


@app.command("specs")
def specs_cmd(
    list_only: bool = typer.Option(False, "--list", help="List scenarios without running"),
    format: str = typer.Option("json", "--format", "-f"),
) -> None:
    """Parse the workspace's feature files (execution lives in the test suite).

    A workspace may execute them whichever way it likes; this reports what the
    files claim and whether every step has a definition in that workspace.
    """
    from . import gherkin

    ws = _ws()
    specs_dir = ws.root / "specs"
    scenarios = gherkin.discover(specs_dir) if specs_dir.is_dir() else []
    _emit(
        {
            "kind": "scutch.specs/v1",
            "features": sorted(str(p.relative_to(specs_dir)) for p in specs_dir.rglob("*.feature")) if specs_dir.is_dir() else [],
            "scenarios": [
                {"feature": s.feature, "name": s.name, "steps": len(s.steps)}
                for s in scenarios
            ],
            "count": len(scenarios),
        },
        format,
    )


@app.command("new")
def new_cmd(
    kind: str = typer.Argument(..., help="operation | feature"),
    name: str = typer.Argument(..., help="Dotted name, e.g. saleor.grist.master.write"),
    effect: str = typer.Option("read", "--effect", help="read | plan_apply | mutate"),
    query: bool = typer.Option(False, "--query", help="This operation computes over data: scaffold a .sql file"),
    domain: str | None = typer.Option(None, "--domain", help="Feature folder (defaults to the name's first segment)"),
    format: str = typer.Option("json", "--format", "-f"),
) -> None:
    """Scaffold a compliant operation or feature.

    The point is that the *shape* is decided here, not by the author: the
    decorator, the query file, the plan stub and the feature skeleton all come
    from the conventions declared in scutch.toml. An agent fills in bodies; it
    does not have to remember the rules, and `doctor` will catch it if it
    deviates.
    """
    from .scaffold import scaffold_feature, scaffold_operation

    ws = _ws()
    try:
        if kind == "operation":
            written = scaffold_operation(ws, name, effect=effect, query=query)
        elif kind == "feature":
            written = scaffold_feature(ws, name, domain=domain)
        else:
            raise UsageError(f"unknown kind {kind!r}", hint="use `operation` or `feature`")
    except ScutchError as exc:
        _fail(exc)
    _emit(
        {
            "kind": "scutch.new/v1",
            "scaffolded": kind,
            "name": name,
            "written": [str(p.relative_to(ws.root)) for p in written],
            "next": [
                "fill in the bodies",
                "scutch doctor",
                f"scutch workflow {domain or name.split('.')[0]}",
            ],
        },
        format,
    )



def _entry() -> None:
    """Console-script entry point. ``main`` is the Typer callback, not the app."""
    app()


if __name__ == "__main__":
    app()
