"""The browser, on Textual. Four screens, the same four steps an agent takes.

Textual does the work that is tedious to hand-roll: scrollable lists, focus, key
bindings, a data table that sizes its own columns, and a layout that survives a
resize. This file is therefore mostly *what* to show; the widgets handle *how*.

The flow is unchanged and it is not reimplemented here — every transition calls
``run.plan`` / ``run.apply``, the same functions the CLI calls, so the digest on
the review screen is the digest the CLI would print.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
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
    Static,
)

from . import catalogue
from .io import ScutchError, Unapproved
from .run import apply as apply_plan
from .run import plan as make_plan


def effect_lines(effects: list[str]) -> list[str]:
    """The plan in words. Reads the kinds it knows; states anything else as is."""
    if not effects:
        return ["nothing — it is already in that state."]
    out = []
    for effect in effects:
        change = json.loads(effect)
        kind = change.get("kind")
        if kind == "write":
            out.append(f"write {change['path']}")
        elif kind == "exec":
            out.append("run " + " ".join(change["argv"]))
        elif kind == "http":
            out.append(f"{change.get('method', 'GET')} {change['url']}")
        else:
            out.append(effect)
    return out


def records(result: Any) -> tuple[list[str], list[list[str]]]:
    """The best (columns, rows) a result can offer a DataTable.

    A list of dicts is a table. A dict with one such list inside is that table.
    Anything else is two columns of name and value, which is still browsable.
    """
    if isinstance(result, dict):
        for value in result.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                columns = list(value[0])
                return columns, [[str(row.get(c, "")) for c in columns] for row in value]
        if result:
            return ["field", "value"], [[k, str(v)] for k, v in result.items()]
    if isinstance(result, list) and result and isinstance(result[0], dict):
        columns = list(result[0])
        return columns, [[str(row.get(c, "")) for c in columns] for row in result]
    if result is None:
        return [], []
    return ["value"], [[str(result)]]


class ScutchApp(App):
    """Pick an operation, fill it in, review the effects, apply."""

    TITLE = "Scutch"

    CSS = """
    Screen { background: $surface; }
    #rail { width: 34; border-right: solid $primary; }
    #main { padding: 1 2; }
    .title { text-style: bold; margin-bottom: 1; }
    .muted { color: $text-muted; }
    .safe { color: $success; }
    .warn { color: $warning; }
    .bad { color: $error; }
    Input { margin-bottom: 1; }
    #buttons { height: auto; margin-top: 1; }
    Button { margin-right: 2; }
    DataTable { height: 1fr; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("escape", "back", "Back"),
        Binding("ctrl+r", "refresh", "Refresh"),
    ]

    def __init__(self, root: Path | None = None) -> None:
        super().__init__()
        self.root = Path.cwd() if root is None else Path(root)
        catalogue.seed(self.root)
        self.operations = catalogue.discover(self.root)
        self.operation: catalogue.Operation | None = None
        self.envelope: dict[str, Any] | None = None
        self.inputs: dict[str, Input] = {}
        # The inputs the plan was computed from. Captured here because the form
        # widgets are destroyed when the review screen mounts, and `apply` must
        # be given exactly the inputs that produced the digest.
        self.planned_inputs: dict[str, str] = {}

    # -- layout ------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal():
            with Vertical(id="rail"):
                yield Static("Operations", classes="title")
                yield ListView(id="ops")
            with VerticalScroll(id="main"):
                yield Static("", id="body")
                yield Vertical(id="form")
                yield DataTable(id="grid")
                yield Horizontal(id="buttons")
        yield Footer()

    async def on_mount(self) -> None:
        self.sub_title = str(self.root)
        rail = self.query_one("#ops", ListView)
        for name in sorted(self.operations):
            doc = (self.operations[name].describe()["doc"] or "").splitlines()
            rail.append(ListItem(Label(f"{name}\n[dim]{doc[0] if doc else ''}[/dim]")))
        await self.show_home()

    # -- screens -----------------------------------------------------------

    async def _clear(self) -> None:
        """Empty the mutable panes, awaiting the removal.

        `remove_children` is asynchronous: mounting a new `#back` before it has
        landed collides on the id. Awaiting is the fix Textual expects, rather
        than unique per-mount ids.
        """
        await self.query_one("#form", Vertical).remove_children()
        await self.query_one("#buttons", Horizontal).remove_children()
        self.inputs = {}
        grid = self.query_one("#grid", DataTable)
        grid.display = False
        if grid.row_count:
            grid.clear(columns=True)

    def _body(self, text: str) -> None:
        self.query_one("#body", Static).update(text)

    async def show_home(self) -> None:
        self.operation = None
        self.envelope = None
        await self._clear()
        count = len(self.operations)
        self._body(
            "[b]Scutch[/b]\n\n"
            f"{count} operation(s) in this workspace. Pick one on the left.\n\n"
            "[dim]Each one plans before it acts: you see the effects and the digest, "
            "then approve or go back.[/dim]"
        )

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        name = sorted(self.operations)[self.query_one("#ops", ListView).index or 0]
        await self.open(self.operations[name])

    async def open(self, operation: catalogue.Operation) -> None:
        self.operation = operation
        self.envelope = None
        await self._clear()
        described = operation.describe()
        self._body(
            f"[b]{operation.name}[/b]\n\n{described['doc']}\n\n"
            + ("[green]Reads only.[/green]" if not described["parameters"] and "list" in operation.name else "")
        )
        form = self.query_one("#form", Vertical)
        for parameter in operation.parameters:
            label = parameter["name"] + ("" if parameter["required"] else "  [dim](optional)[/dim]")
            form.mount(Static(label, classes="muted"))
            field = Input(placeholder=parameter["name"], id=f"in-{parameter['name']}")
            self.inputs[parameter["name"]] = field
            form.mount(field)
        buttons = self.query_one("#buttons", Horizontal)
        buttons.mount(Button("Preview effects", id="plan", variant="primary"))
        buttons.mount(Button("Back", id="back"))
        if self.inputs:
            next(iter(self.inputs.values())).focus()

    async def show_review(self) -> None:
        await self._clear()
        assert self.envelope is not None
        lines = "\n".join("  • " + line for line in effect_lines(self.envelope["effects"]))
        self._body(
            "[b]Review — nothing has happened yet[/b]\n\n"
            f"[b]This will:[/b]\n{lines}\n\n"
            f"[dim]digest {self.envelope['digest']}[/dim]"
        )
        buttons = self.query_one("#buttons", Horizontal)
        if self.envelope["effects"]:
            buttons.mount(Button("Apply", id="apply", variant="success"))
            buttons.mount(Button("Back", id="back"))
        else:
            buttons.mount(Button("Back", id="back"))

    async def show_result(self, applied: bool = True) -> None:
        await self._clear()
        assert self.envelope is not None
        self._body("[b]Applied[/b]" if applied else "[b]Result[/b] [dim](read only)[/dim]")
        columns, rows = records(self.envelope.get("result"))
        grid = self.query_one("#grid", DataTable)
        if columns:
            grid.display = True
            grid.add_columns(*columns)
            for row in rows:
                grid.add_row(*row)
        self.query_one("#buttons", Horizontal).mount(Button("Back", id="back"))

    # -- transitions -------------------------------------------------------

    def _values(self) -> dict[str, str]:
        values = {}
        for parameter in (self.operation.parameters if self.operation else []):
            field = self.inputs.get(parameter["name"])
            text = (field.value if field else "").strip()
            if text:
                values[parameter["name"]] = text
        return values

    async def plan_now(self) -> None:
        if not self.operation:
            return
        missing = [
            p["name"] for p in self.operation.parameters
            if p["required"] and p["name"] not in self._values()
        ]
        if missing:
            self.notify("Needed: " + ", ".join(missing), severity="warning")
            return
        self.planned_inputs = self._values()
        try:
            self.envelope = make_plan(
                self.operation.fn, root=self.root, inputs=self.planned_inputs
            )
        except ScutchError as exc:
            self.notify(str(exc), severity="error", timeout=8)
            return

        if self.envelope["effects"]:
            await self.show_review()
            return
        # Nothing to plan means a read: it has already happened during the dry
        # pass, so show what it found rather than asking for approval of nothing.
        await self.show_result(applied=False)

    async def apply_now(self) -> None:
        if not self.operation or not self.envelope:
            return
        try:
            self.envelope = apply_plan(
                self.operation.fn,
                root=self.root,
                inputs=self.planned_inputs,
                approve=self.envelope["digest"],
            )
        except Unapproved as exc:
            self.notify(str(exc), severity="error", timeout=8)
            return
        except ScutchError as exc:
            self.notify(str(exc), severity="error", timeout=8)
            return
        await self.show_result()

    # -- events ------------------------------------------------------------

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "plan":
            await self.plan_now()
        elif event.button.id == "apply":
            await self.apply_now()
        elif event.button.id == "back":
            await self.action_back()

    async def action_back(self) -> None:
        if self.envelope is not None:
            self.envelope = None
            if self.operation:
                await self.open(self.operation)
                return
        await self.show_home()

    async def action_refresh(self) -> None:
        self.operations = catalogue.discover(self.root)
        rail = self.query_one("#ops", ListView)
        rail.clear()
        for name in sorted(self.operations):
            doc = (self.operations[name].describe()["doc"] or "").splitlines()
            rail.append(ListItem(Label(f"{name}\n[dim]{doc[0] if doc else ''}[/dim]")))
        await self.show_home()


def run(root: Path | None = None) -> int:
    ScutchApp(root).run()
    return 0
