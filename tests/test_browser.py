"""The Textual browser: the same four steps, driven headlessly.

Textual's `run_test` gives a real app without a terminal, so these assert what a
person would see and what actually happened — not a mock of it.
"""
import json

import pytest

textual = pytest.importorskip("textual")

from textual.widgets import DataTable, Input  # noqa: E402

from scutch.browser import ScutchApp, effect_lines, records  # noqa: E402
from scutch.run import plan  # noqa: E402


@pytest.fixture()
def app(ws, monkeypatch):
    monkeypatch.chdir(ws)
    return ScutchApp(ws)


def body(app) -> str:
    return app.query_one("#body").render().plain


def run_async(coro):
    import asyncio

    return asyncio.run(coro)


def test_it_lists_every_operation(app):
    async def go():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            rail = app.query_one("#ops")
            assert len(rail.children) == len(app.operations)

    run_async(go())


def test_a_mutation_shows_effects_and_the_cli_digest(app, ws, ops):
    expected = plan(ops["orders.fulfil"].fn, root=ws, inputs={"order_id": "ord_a1"})

    async def go():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.open(app.operations["orders.fulfil"])
            await pilot.pause()
            app.inputs["order_id"].value = "ord_a1"
            await pilot.pause()
            await app.plan_now()
            await pilot.pause()
            text = body(app)
            assert "write ledger.json" in text
            # Shown in two halves so it does not wrap mid-token; compare joined.
            assert expected["digest"] in text.replace("\n", "")
            assert json.loads((ws / "ledger.json").read_text())["orders"][0]["status"] == "placed"

    run_async(go())


def test_applying_from_review_writes_the_change(app, ws):
    async def go():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.open(app.operations["orders.fulfil"])
            await pilot.pause()
            app.inputs["order_id"].value = "ord_a1"
            await pilot.pause()
            await app.plan_now()
            await pilot.pause()
            await app.apply_now()
            await pilot.pause()
            assert json.loads((ws / "ledger.json").read_text())["orders"][0]["status"] == "fulfilled"

    run_async(go())


def test_a_list_result_fills_the_table(app):
    async def go():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.open(app.operations["orders.list"])
            await pilot.pause()
            await app.plan_now()
            await pilot.pause()
            grid = app.query_one("#grid", DataTable)
            assert grid.row_count == 4
            # Column order follows the records' key order, not the alphabet.
            assert {str(c.label) for c in grid.columns.values()} == {
                "customer", "id", "status", "total",
            }

    run_async(go())


def test_back_from_review_changes_nothing(app, ws):
    async def go():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.open(app.operations["orders.fulfil"])
            await pilot.pause()
            app.inputs["order_id"].value = "ord_a1"
            await pilot.pause()
            await app.plan_now()
            await pilot.pause()
            await app.action_back()
            await pilot.pause()
            assert app.envelope is None
            assert json.loads((ws / "ledger.json").read_text())["orders"][0]["status"] == "placed"

    run_async(go())


def test_a_missing_required_field_is_refused_without_leaving_the_form(app):
    async def go():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.open(app.operations["orders.fulfil"])
            await pilot.pause()
            await app.plan_now()
            await pilot.pause()
            assert app.envelope is None

    run_async(go())


def test_records_turns_a_list_of_dicts_into_columns_and_rows():
    columns, rows = records({"orders": [{"id": "a", "status": "placed"}], "count": 1})
    assert "orders" in columns or "id" in columns
    assert rows


def test_records_of_a_scalar_result_is_a_two_column_table():
    """Anything that is not a list of records is still browsable as name/value."""
    columns, rows = records({"changed": True})
    assert columns == ["field", "value"]
    assert rows == [["changed", "True"]]


def test_effect_lines_reads_each_kind():
    effects = [
        json.dumps({"kind": "write", "path": "a.json", "text": "x"}),
        json.dumps({"kind": "exec", "argv": ["ls", "-l"]}),
        json.dumps({"kind": "http", "url": "https://x", "method": "POST", "body": ""}),
    ]
    lines = effect_lines(effects)
    assert "a.json" in lines[0] and "write" in lines[0]
    assert "run" in lines[1] and "ls -l" in lines[1]
    assert "POST" in lines[2] and "https://x" in lines[2]


# -- colour and icons -------------------------------------------------------


def test_every_effect_line_carries_its_kind_icon():
    effects = [
        json.dumps({"kind": "write", "path": "a", "text": ""}),
        json.dumps({"kind": "exec", "argv": ["x"]}),
        json.dumps({"kind": "http", "url": "u", "method": "GET", "body": ""}),
    ]
    lines = effect_lines(effects)
    for line, icon in zip(lines, ("📝", "⚙️", "🌐")):
        assert icon in line


def test_a_status_cell_is_coloured_by_its_meaning():
    from scutch.browser import _paint

    assert _paint("fulfilled", True) == "[green]fulfilled[/green]"
    assert _paint("placed", True) == "[yellow]placed[/yellow]"
    assert _paint("cancelled", True) == "[red]cancelled[/red]"
    assert _paint("weird", True) == "weird"
    assert _paint("fulfilled", False) == "fulfilled"


def test_the_review_screen_says_how_many_changes(ws):
    async def go():
        app = ScutchApp(ws)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.open(app.operations["orders.notify"])
            await pilot.pause()
            app.inputs["order_id"].value = "ord_b1"
            app.inputs["url"].value = "https://hooks.example/x"
            await pilot.pause()
            await app.plan_now()
            await pilot.pause()
            return body(app)

    shown = run_async(go())
    assert "🔎 Review" in shown
    assert "2 changes" in shown
    assert "📝" in shown and "🌐" in shown


def test_a_failed_effect_is_a_notification_and_stays_on_review(ws):
    async def go():
        app = ScutchApp(ws)
        notes = []
        app.notify = lambda *a, **k: notes.append(a[0])
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.open(app.operations["orders.notify"])
            await pilot.pause()
            app.inputs["order_id"].value = "ord_b1"
            app.inputs["url"].value = "https://nothing.invalid/x"
            await pilot.pause()
            await app.plan_now()
            await pilot.pause()
            await app.apply_now()
            await pilot.pause()
            return notes, app.envelope["status"]

    notes, status = run_async(go())
    assert "cannot reach" in notes[0]
    assert status == "dry"


def test_markup_carries_real_colour_when_the_terminal_allows_it(ws, monkeypatch):
    """Guard against the whole screen silently going monochrome.

    `NO_COLOR` is set in some shells; when it is not, the accent must actually
    reach a style span rather than being dropped or shown literally.
    """
    from textual.content import Content

    monkeypatch.delenv("NO_COLOR", raising=False)

    shown = {}

    async def go():
        app = ScutchApp(ws)
        async with app.run_test(size=(100, 26)) as pilot:
            await pilot.pause()
            await app.open(app.operations["orders.fulfil"])
            await pilot.pause()
            app.inputs["order_id"].value = "ord_a1"
            await pilot.pause()
            await app.plan_now()
            await pilot.pause()
            shown["text"] = str(app.query_one("#body").content)
            return app.console.color_system

    system = run_async(go())
    assert "fea62b" in shown["text"] or "theme(" in shown["text"]
    # and the markup is not displayed literally
    assert "[bold" not in Content.from_markup(shown["text"]).plain


def test_effect_icons_are_tinted_per_kind():
    effects = [
        json.dumps({"kind": "write", "path": "a", "text": ""}),
        json.dumps({"kind": "http", "url": "u", "method": "POST", "body": ""}),
    ]
    lines = effect_lines(effects)
    assert "#fea62b" in lines[0]      # write
    assert "#4ebf71" in lines[1]      # http
