"""The human interface: a guided terminal app.

This is a *projection* of the same contracts and envelopes the CLI returns. It
calls ``runner.execute`` and ``gherkin.run_feature`` exactly as ``scutch run``
and ``scutch workflow`` do, so approving here is ``--approve-plan <digest>`` and
nothing else. There is no second engine and no hidden memory: a run record is
the only state, so quitting and restarting loses nothing.

Built for a person, not an agent:

  * one screen at a time, each explaining where you are;
  * operations listed by summary, labelled by effect ("Reads only");
  * a form generated from the schema, with hints from the same schema;
  * a plain-language summary of the change before anything happens;
  * results as labelled rows, with the command that reproduces them.

The agent experience stays the CLI: envelope in, ``next.argv`` out, no prompts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import human
from .errors import ScutchError

try:  # pragma: no cover - import-time capability probe
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.widgets import Button, Footer, Header, Input, Label, ListItem, ListView, Static

    HAVE_TUI = True
except Exception:  # noqa: BLE001
    HAVE_TUI = False


def available() -> bool:
    """Whether the terminal interface can run here."""
    return HAVE_TUI


if HAVE_TUI:

    class ScutchApp(App):
        """Guided operation runner."""

        CSS = """
        Screen { background: $surface; }
        #body { height: 1fr; }
        #rail { width: 40; border-right: solid $primary; padding: 0 1; }
        #main { padding: 1 2; }
        .h1 { text-style: bold; margin-bottom: 1; }
        .hint { color: $text-muted; }
        .safe { color: $success; }
        .caution { color: $warning; }
        .bad { color: $error; }
        .label { text-style: bold; margin-top: 1; }
        .fieldhint { color: $text-muted; margin-bottom: 0; }
        Input { border: tall $primary; margin-bottom: 1; }
        #actions { height: auto; margin-top: 1; }
        Button { margin-right: 2; }
        """

        BINDINGS = [
            Binding("q", "quit", "Quit"),
            Binding("escape", "back", "Back"),
            Binding("r", "refresh", "Refresh"),
        ]

        def __init__(self, ws, *, feature: Path | None = None, operation: str | None = None) -> None:
            super().__init__()
            self.ws = ws
            self.initial_feature = feature
            self.initial_operation = operation
            self.stack: list[str] = ["home"]
            self.current_op: str | None = None
            self.op_card: dict[str, Any] | None = None
            self.values: dict[str, Any] = {}
            self.last_envelope: dict[str, Any] | None = None
            self.pending_workflow: Path | None = None
            # Textual ids may only contain letters, digits, `_` and `-`, so the
            # dotted operation name is kept in a map keyed by a slug.
            self.rail_names: dict[str, str] = {}
            # Every generated widget gets a unique id (Textual forbids duplicates
            # while an old one is still unmounting), and `inputs` maps a field
            # name to its current widget so callers never parse ids.
            self.generation = 0
            self.inputs: dict[str, Any] = {}

        # -- composition ---------------------------------------------------

        def compose(self) -> ComposeResult:
            yield Header(show_clock=False)
            with Horizontal(id="body"):
                with Vertical(id="rail"):
                    yield Static("What would you like to do?", classes="h1")
                    yield ListView(id="ops")
                with VerticalScroll(id="main"):
                    yield Static("", id="content")
            yield Footer()

        def on_mount(self) -> None:
            self.title = "Scutch"
            self.sub_title = str(self.ws.root)
            self._fill_rail()
            if self.initial_operation:
                self.open_operation(self.initial_operation)
            elif self.initial_feature:
                self.show_workflow(self.initial_feature)
            else:
                self.show_home()

        # -- left rail -----------------------------------------------------

        def _fill_rail(self) -> None:
            lv = self.query_one("#ops", ListView)
            self.rail_names = {}
            for index, op in enumerate(sorted(self.ws.registry.all(), key=lambda o: (o.name.split(".")[0], o.name))):
                slug = f"op-{index}"
                self.rail_names[slug] = op.name
                lv.append(ListItem(Label(op.summary[:36]), id=slug))

        def on_list_view_selected(self, event: ListView.Selected) -> None:
            if event.item.id in self.rail_names:
                self.open_operation(self.rail_names[event.item.id])

        # -- content helpers -----------------------------------------------

        def _content(self, text: str) -> None:
            self.query_one("#content", Static).update(text)

        def _clear_extra(self) -> None:
            """Remove form inputs and action rows, leaving #content in place.

            Removal is applied asynchronously, so generated rows carry a unique
            id (see `_actions`) rather than a fixed `#actions`: a new row can
            never collide with one that is still being unmounted.
            """
            main = self.query_one("#main", VerticalScroll)
            for child in [c for c in main.children if getattr(c, "id", "") != "content"]:
                child.remove()

        def _actions(self, *buttons: tuple[str, str, str]) -> None:
            """Mount an action row: (id, label, variant).

            The row id is unique per mount so an in-flight unmount of the
            previous row cannot cause a duplicate-id collision.
            """
            main = self.query_one("#main", VerticalScroll)
            self._action_rows = getattr(self, "_action_rows", 0) + 1
            row = Horizontal(id=f"actions-{self._action_rows}")
            main.mount(row)
            for button_id, label, variant in buttons:
                row.mount(Button(label, id=button_id, variant=variant))

        # -- home ----------------------------------------------------------

        def show_home(self) -> None:
            self.current_op = None
            self.op_card = None
            self.stack = ["home"]
            self._clear_extra()
            parts = [
                "[b]Scutch[/b]",
                "",
                "This repository is a catalogue of operations — small, typed tasks with",
                "a written contract. Pick one on the left: it explains what it does and",
                "what it needs before anything happens.",
                "",
            ]
            workflows = self._workflows()
            if workflows:
                parts.append("[b]Workflows[/b] — several steps in order:")
                for path in workflows:
                    summary = human.workflow_summary(self.ws, path)
                    parts.append(f"  {summary['name']}  ({summary['count']} steps)")
                parts.append("")
                parts.append("Choose one below to see its steps.")
            parts.append("")
            try:
                from . import discovery

                report = discovery.doctor(self.ws)
                bad = [c for c in report["checks"] if not c["ok"]]
            except Exception:  # noqa: BLE001
                bad = []
            if bad:
                parts.append(f"[yellow]{len(bad)} check(s) need attention[/yellow] — press d")
            else:
                parts.append("[green]Everything checks out.[/green]")
            self._content("\n".join(parts))
            if workflows:
                self._workflow_buttons = {f"wf-{i}": p for i, p in enumerate(workflows)}
                self._actions(*[(f"wf-{i}", p.stem, "default") for i, p in enumerate(workflows)])

        def _workflows(self) -> list[Path]:
            root = self.ws.root / self.ws.conventions.features_root
            return sorted(root.rglob("*.feature")) if root.is_dir() else []

        # -- operation -----------------------------------------------------

        def open_operation(self, name: str) -> None:
            try:
                op = self.ws.registry.get(name)
            except ScutchError as exc:
                self._content(f"[red]{exc}[/red]")
                return
            self.current_op = name
            self.stack = ["home", f"op:{name}"]
            self.op_card = human.describe_operation(op)
            card = self.op_card
            self.values = {
                p["name"]: p.get("default")
                for p in card["required"] + card["optional"]
                if p.get("default") is not None
            }
            self._clear_extra()
            lines = [
                f"[b]{card['title']}[/b]",
                f"[dim]{card['name']}[/dim]",
                "",
            ]
            if card["about"]:
                lines += [card["about"], ""]
            effect_class = "safe" if card["safe"] else "caution"
            lines.append(f"[{effect_class}]{card['effect']}[/{effect_class}]")
            if card["providers"]:
                lines.append(f"[dim]Needs: {', '.join(card['providers'])}[/dim]")
            lines.append("")
            lines.append("[b]What it needs[/b]")
            self._content("\n".join(lines))

            main = self.query_one("#main", VerticalScroll)
            self.generation += 1
            gen = self.generation
            self.inputs = {}
            for index, param in enumerate(card["required"] + card["optional"]):
                pname = param["name"]
                marker = "" if param["required"] else " (optional)"
                main.mount(Static(f"[b]{pname.replace('_', ' ').capitalize()}[/b]{marker}", classes="label", id=f"lbl-{gen}-{index}"))
                hint = human.field_help(pname, param["schema"])
                if hint:
                    main.mount(Static(hint, classes="fieldhint", id=f"hint-{gen}-{index}"))
                current = self.values.get(pname)
                widget = Input(
                    value="" if current is None else str(current),
                    placeholder=pname,
                    id=f"in-{gen}-{index}",
                )
                self.inputs[pname] = widget
                main.mount(widget)
            label = "Run it" if card["safe"] else "Preview what will change"
            self._actions(("go", label, "primary"), ("back", "Back", "default"))

        def _field_name(self, widget) -> str | None:
            for name, current in self.inputs.items():
                if current is widget:
                    return name
            return None

        def on_input_changed(self, event: Input.Changed) -> None:
            name = self._field_name(event.input)
            if name is not None:
                self.values[name] = self._coerce(name, event.value)

        def _coerce(self, name: str, raw: str) -> Any:
            if self.op_card is None:
                return raw
            for param in self.op_card["required"] + self.op_card["optional"]:
                if param["name"] != name:
                    continue
                schema = param["schema"]
                typ = schema.get("type")
                if raw.strip() == "":
                    return None
                if "enum" in schema:
                    return raw.strip()
                try:
                    if typ == "integer":
                        return int(raw)
                    if typ == "number":
                        return float(raw)
                except ValueError:
                    return raw
                if typ == "boolean":
                    return raw.strip().lower() in ("true", "yes", "1")
                if typ == "array":
                    return [v.strip() for v in raw.split(",") if v.strip()]
                return raw
            return raw

        # -- transitions ---------------------------------------------------

        def submit(self) -> None:
            if not self.current_op:
                return
            from .runner import execute

            payload = {k: v for k, v in self.values.items() if v is not None}
            try:
                env, _ = execute(self.ws, self.current_op, payload, fmt="json")
            except ScutchError as exc:
                self._content(f"[red]{exc}[/red]")
                return
            self.last_envelope = env
            self.show_envelope(env)

        def approve(self) -> None:
            if not self.current_op or not self.last_envelope:
                return
            from .runner import execute

            digest = (self.last_envelope.get("plan") or {}).get("digest")
            if not digest:
                return
            payload = {k: v for k, v in self.values.items() if v is not None}
            try:
                env, _ = execute(self.ws, self.current_op, payload, approve_plan=digest, fmt="json")
            except ScutchError as exc:
                self._content(f"[red]{exc}[/red]")
                return
            self.last_envelope = env
            self.show_envelope(env)

        def show_envelope(self, env: dict[str, Any]) -> None:
            self._clear_extra()
            out = human.outcome(env)
            status = out["status"]
            lines = [f"[b]{out['label']}[/b]", ""]

            if status == "needs_input":
                missing = env.get("missing") or {}
                names = list(missing) if isinstance(missing, dict) else list(missing)
                lines.append("Still needed: " + ", ".join(names))
                violations = ((env.get("error") or {}).get("details") or {}).get("violations") or []
                for violation in violations:
                    lines.append(f"[red]{violation.get('path')}: {violation.get('message')}[/red]")
                lines += ["", "Fix those values, then continue."]

            elif status == "needs_approval":
                lines.append("[b]This is what will change[/b]")
                lines.append("")
                for sentence in out["plan"]:
                    lines.append(f"  • {sentence}")
                digest = (env.get("plan") or {}).get("digest", "")
                lines += ["", f"[dim]approval reference {digest}[/dim]", ""]
                lines.append("Nothing has been changed yet.")

            elif status == "complete":
                for sentence in out["plan"]:
                    lines.append(f"  • {sentence}")
                if out["rows"]:
                    lines.append("")
                    for label, value in out["rows"]:
                        lines.append(f"  {label}: {value}")
                if out["redacted"]:
                    lines += ["", "[dim]Held back from the summary: " + ", ".join(out["redacted"]) + "[/dim]"]

            elif status == "failed":
                if out["happened"]:
                    lines.append(f"[yellow]{out['happened']}[/yellow]")
                if out["what_to_do"]:
                    lines += ["", out["what_to_do"]]
                if out["rows"]:
                    lines.append("")
                    for label, value in out["rows"]:
                        lines.append(f"  {label}: {value}")

            else:  # running, partial, cancelled
                lines.append(out["happened"] or "This is still in progress.")
                if out["job_id"]:
                    lines.append(f"[dim]reference {out['job_id']}[/dim]")

            argv = " ".join(env.get("next", {}).get("argv", []))
            if argv:
                lines += ["", "[dim]The same thing from the command line:[/dim]", f"[dim]  {argv}[/dim]"]
            self._content("\n".join(lines))

            if status == "needs_approval":
                self._actions(("approve", "Approve", "success"), ("back", "Cancel", "error"))
            else:
                self._actions(("back", "Back", "default"))

        # -- workflows -----------------------------------------------------

        def show_workflow(self, path: Path) -> None:
            summary = human.workflow_summary(self.ws, path)
            self.pending_workflow = path
            self.current_op = None
            self.stack = ["home", f"wf:{path}"]
            self._clear_extra()
            lines = [f"[b]{summary['name']}[/b]", f"[dim]{summary['path']}[/dim]", "", "[b]Steps[/b]"]
            for index, name in enumerate(summary["steps"], 1):
                lines.append(f"  {index}. {name}")
            lines += [
                "",
                "They run in order, one workspace, stopping at the first failure —",
                "a later step must not run on broken state.",
            ]
            self._content("\n".join(lines))
            self._actions(("runwf", "Run all steps", "primary"), ("back", "Back", "default"))

        def run_workflow(self) -> None:
            from . import gherkin
            from .harness import LocalHarness
            from .steps import LIBRARY, Steps

            path = self.pending_workflow
            if path is None:
                return
            self._clear_extra()
            self._content("[b]Running…[/b]")
            scenarios = gherkin.parse_feature(path)
            run = gherkin.run_feature(scenarios, Steps(LocalHarness(self.ws)), LIBRARY)
            lines = ["[b]Finished[/b]" if run.ok else "[b][yellow]Stopped[/yellow][/b]", ""]
            for result in getattr(run, "results", []):
                name, ok = (result if isinstance(result, tuple) else (getattr(result, "name", str(result)), getattr(result, "ok", True)))
                lines.append(f"  {'✔' if ok else '✖'} {name}")
            if not run.ok:
                failed = getattr(run, "failed_at", None)
                if failed:
                    lines += ["", f"It stopped at: {failed}"]
                lines.append("")
                lines.append("Nothing after that step was run.")
                lines.append("[dim]The run record has the rest of the detail.[/dim]")
            else:
                lines += ["", f"All {len(scenarios)} steps ran."]
            self._content("\n".join(lines))
            self._actions(("back", "Back", "default"))

        # -- events --------------------------------------------------------

        def on_button_pressed(self, event: Button.Pressed) -> None:
            button_id = event.button.id or ""
            if button_id == "back":
                self.action_back()
            elif button_id == "go":
                self.submit()
            elif button_id == "approve":
                self.approve()
            elif button_id == "runwf":
                self.run_workflow()
            elif button_id in getattr(self, "_workflow_buttons", {}):
                self.show_workflow(self._workflow_buttons[button_id])

        def action_back(self) -> None:
            if self.current_op and self.last_envelope is not None:
                self.last_envelope = None
                self.open_operation(self.current_op)
                return
            if len(self.stack) > 1:
                previous = self.stack[-2]
                if previous.startswith("wf:"):
                    self.show_workflow(Path(previous[3:]))
                    return
            self.show_home()

        def action_refresh(self) -> None:
            if self.current_op:
                self.open_operation(self.current_op)
            elif self.pending_workflow:
                self.show_workflow(self.pending_workflow)
            else:
                self.show_home()


def run_ui(ws, *, feature: Path | None = None, operation: str | None = None) -> None:
    """Start the human interface. Raises RuntimeError when Textual is absent."""
    if not HAVE_TUI:
        raise RuntimeError("the terminal interface needs `textual`, which is a declared dependency; reinstall scutch")
    ScutchApp(ws, feature=feature, operation=operation).run()
