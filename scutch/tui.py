"""Terminal interface.

The TUI is a *projection*, not a second implementation. Every action it offers
calls the same ``runner.execute`` / ``runner.status`` functions the CLI calls,
with the same envelopes, digests and run records. Approving here is exactly
``--approve-plan <digest>``.

It never needs a credential value: providers are resolved inside the worker,
and descriptions only ever show provider handles.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

from .contract import Operation
from .runner import execute, status as status_cmd

try:  # pragma: no cover - import-time capability probe
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.widgets import (
        Button,
        DataTable,
        Footer,
        Header,
        Input,
        Label,
        ListItem,
        ListView,
        Markdown,
        RichLog,
        Static,
        Switch,
    )

    HAVE_TUI = True
except Exception:  # noqa: BLE001
    HAVE_TUI = False


def available() -> bool:
    return HAVE_TUI


# --------------------------------------------------------------------------
# Widget ids: operation names are dotted, Textual ids allow only [A-Za-z0-9_-].
# Slugs are reversible so selection never depends on list order.


def slug(name: str) -> str:
    return "op_" + name.replace(".", "__").replace("-", "_h")


def unslug(widget_id: str) -> str:
    assert widget_id.startswith("op_")
    return widget_id[3:].replace("__", ".").replace("_h", "-")


def field_id(name: str, generation: int = 0) -> str:
    return f"f{generation}_" + name.replace("-", "_h").replace(".", "__")


# --------------------------------------------------------------------------
# Form model (works headless, so it is testable without a terminal)


@dataclasses.dataclass
class FormField:
    name: str
    required: bool
    kind: str  # string | integer | number | boolean | enum
    enum: list[Any] | None
    default: Any
    description: str
    value: str = ""

    @classmethod
    def from_parameter(cls, op: Operation, param: dict[str, Any]) -> "FormField":
        schema = param["schema"]
        kind = schema.get("type", "string")
        return cls(
            name=param["name"],
            required=param.get("required", False),
            kind=kind if "enum" not in schema else "enum",
            enum=schema.get("enum"),
            default=param.get("default"),
            description=schema.get("description", ""),
        )


def build_form(op: Operation) -> list[FormField]:
    d = op.describe()
    return [FormField.from_parameter(op, p) for p in d["parameters"]]


def form_to_input(fields: list[FormField]) -> dict[str, Any]:
    """Convert displayed values into typed JSON. Blank optional fields drop out."""
    out: dict[str, Any] = {}
    for f in fields:
        raw = f.value.strip()
        if raw == "":
            if f.required:
                raise ValueError(f"{f.name} is required")
            continue
        if f.kind == "integer":
            out[f.name] = int(raw)
        elif f.kind == "number":
            out[f.name] = float(raw)
        elif f.kind == "boolean":
            out[f.name] = raw.lower() in ("1", "true", "yes", "on")
        else:
            out[f.name] = raw
    return out


def describe_card(op: Operation) -> str:
    lines = [
        f"## {op.name}",
        "",
        op.summary,
        "",
        f"**effect** `{op.effect}`  ·  **approval** `{op.approval}`",
    ]
    if op.credentials:
        lines.append(
            "**providers** " + ", ".join(f"`{v.name}`" for v in op.credentials.values())
        )
    if op.idempotency_key:
        lines.append("**idempotency** " + ", ".join(f"`{k}`" for k in op.idempotency_key))
    lines += ["", "| parameter | type | required |", "|---|---|---|"]
    for p in op.describe()["parameters"]:
        lines.append(
            f"| `{p['name']}` | `{p['schema'].get('type', 'any')}` | {p['required']} |"
        )
    if op.description:
        lines += ["", op.description]
    return "\n".join(lines)


def envelope_markdown(env: dict[str, Any]) -> str:
    lines = [f"### {env['status']}", ""]
    if env.get("plan"):
        plan = env["plan"]
        if plan.get("summary"):
            lines += [plan["summary"], ""]
        lines += ["```diff"]
        for c in plan.get("changes", []):
            target = f" {c['target']}" if c.get("target") else ""
            lines.append(f"+ {c['kind']}{target} = {json.dumps(c.get('value'))}")
        if not plan.get("changes"):
            lines.append("(no changes — nothing will be mutated)")
        lines += ["```", ""]
        if plan.get("digest"):
            lines += [f"digest `{plan['digest']}`", ""]
    if env.get("result") is not None:
        lines += ["```json", json.dumps(env["result"], indent=2), "```", ""]
    if env.get("redacted"):
        lines += [f"restricted fields withheld: `{', '.join(env['redacted'])}`", ""]
    if env.get("artifacts"):
        lines += [
            f"restricted artifact: `{a['path']}`" for a in env["artifacts"]
        ] + [""]
    if env.get("error"):
        err = env["error"]
        lines += [f"**{err.get('code')}** — {err.get('message')}", ""]
        if err.get("hint"):
            lines += [f"hint: {err['hint']}", ""]
    if env.get("next", {}).get("argv"):
        lines += ["equivalent command:", "", "```bash", " ".join(env["next"]["argv"]), "```"]
        lines += ["input:", "", "```json", json.dumps(env["next"].get("input"), indent=2), "```"]
    if env.get("notes"):
        lines += [f"- {n}" for n in env["notes"]]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# App


if HAVE_TUI:

    class ScutchApp(App[None]):
        """Searchable operation cards → generated form → plan → approve → inspect."""

        CSS = """
        Screen { layers: base; }
        #body { height: 1fr; }
        #sidebar { width: 38; border-right: solid $panel; }
        #detail { width: 1fr; }
        #form { height: auto; max-height: 18; border: round $panel; padding: 0 1; }
        #actions { height: auto; padding: 0 1; }
        #log { height: 1fr; border: round $panel; }
        .field-label { color: $text-muted; }
        Input { margin-bottom: 0; }
        """

        BINDINGS = [
            ("r", "run_operation", "Run / preview"),
            ("a", "approve", "Approve plan"),
            ("i", "inspect_runs", "Runs"),
            ("q", "quit", "Quit"),
        ]

        def __init__(self, ws):
            super().__init__()
            self.ws = ws
            self.current: Operation | None = None
            self.fields: list[FormField] = []
            self.last_envelope: dict[str, Any] | None = None
            self.pending_approval: str | None = None
            # Remounting a form reuses parameter names; a generation prefix
            # keeps widget ids unique without relying on deferred removal.
            self._form_generation = 0

        # -- layout ------------------------------------------------------

        def compose(self) -> ComposeResult:
            yield Header(show_clock=False)
            with Horizontal(id="body"):
                with Vertical(id="sidebar"):
                    yield Label("operations", classes="field-label")
                    yield ListView(id="ops")
                with VerticalScroll(id="detail"):
                    yield Markdown("select an operation", id="card")
                    with Vertical(id="form"):
                        yield Label("inputs", classes="field-label")
                        yield Vertical(id="fields")
                    with Horizontal(id="actions"):
                        yield Button("Preview / run", id="run", variant="primary")
                        yield Button("Approve plan", id="approve", variant="warning", disabled=True)
                        yield Input(placeholder="idempotency key (optional)", id="idem")
                    yield RichLog(id="log", markup=False, wrap=True)
            yield Footer()

        def on_mount(self) -> None:
            self.title = "scutch"
            self.sub_title = str(self.ws.root)
            lv = self.query_one("#ops", ListView)
            for op in self.ws.registry.all():
                lv.append(ListItem(Label(f"{op.name}\n  {op.effect}"), id=slug(op.name)))
            if self.ws.registry.all():
                self.select_operation(self.ws.registry.all()[0].name)

        # -- selection ---------------------------------------------------

        def on_list_view_selected(self, event: ListView.Selected) -> None:
            if event.item.id:
                self.select_operation(unslug(event.item.id))

        def select_operation(self, name: str) -> None:
            if self.current is not None and self.current.name == name:
                return
            self.current = self.ws.registry.get(name)
            self.pending_approval = None
            self.query_one("#card", Markdown).update(describe_card(self.current))
            self.fields = build_form(self.current)
            self._form_generation += 1
            holder = self.query_one("#fields")
            for child in list(holder.children):
                child.remove()
            for f in self.fields:
                label = f"{f.name}{'' if f.required else ' (optional)'}"
                if f.enum:
                    label += f"  [{ ' | '.join(map(str, f.enum)) }]"
                holder.mount(Label(label, classes="field-label"))
                holder.mount(
                    Input(
                        value="" if f.default is None else str(f.default),
                        placeholder=f.description or f.name,
                        id=field_id(f.name, self._form_generation),
                    )
                )
            self.query_one("#approve", Button).disabled = True
            self.query_one("#log", RichLog).clear()

        # -- actions -----------------------------------------------------

        def _collect(self) -> dict[str, Any] | None:
            assert self.current is not None
            for f in self.fields:
                try:
                    f.value = self.query_one(
                        f"#{field_id(f.name, self._form_generation)}", Input
                    ).value
                except Exception:  # noqa: BLE001 - widget vanished
                    f.value = ""
            try:
                return form_to_input(self.fields)
            except ValueError as exc:
                self._write(f"input error: {exc}")
                return None

        def action_run_operation(self) -> None:
            self._run(approve=None)

        def action_approve(self) -> None:
            if self.pending_approval is None:
                self._write("nothing to approve: preview a plan first")
                return
            self._run(approve=self.pending_approval)

        def _run(self, approve: str | None) -> None:
            if self.current is None:
                return
            payload = self._collect()
            if payload is None:
                return
            idem = self.query_one("#idem", Input).value.strip() or f"ui-{self.current.name}"
            env, code = execute(
                self.ws,
                self.current.name,
                payload,
                approve_plan=approve,
                idempotency_key=idem,
                fmt="json",
            )
            self.last_envelope = env
            self._write(envelope_markdown(env))
            needs = env["status"] == "needs_approval" and not env.get("error")
            self.pending_approval = env["plan"]["digest"] if needs else None
            self.query_one("#approve", Button).disabled = not needs
            if env["status"] == "needs_approval" and not needs:
                self._write("approval mismatch: nothing was applied. Approve the digest above.")

        def action_inspect_runs(self) -> None:
            from .runs import RunStore

            store = RunStore(self.ws.runs_dir)
            log = self.query_one("#log", RichLog)
            log.clear()
            log.write("recent runs\n")
            for rec in store.recent(limit=15):
                log.write(
                    f"{rec.run_id}  {rec.operation}  {rec.status}/{rec.phase}  "
                    f"job={rec.job_id}"
                )
            self._write("\npress `r` to run, `q` to quit")

        def _write(self, text: str) -> None:
            log = self.query_one("#log", RichLog)
            log.write(text)

    def run_app(ws) -> None:
        ScutchApp(ws).run()

else:  # pragma: no cover

    def run_app(ws) -> None:  # type: ignore[misc]
        raise SystemExit(
            "textual is not installed: pip install 'scutch[tui]' or use the JSON interface"
        )
