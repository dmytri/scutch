"""The TUI is a projection: drive it and assert the same transitions the CLI makes.

These run Textual headlessly (no terminal), so they check the *flow* — the thing
a person experiences — not just plumbing.
"""

from __future__ import annotations

import pytest

pytest.importorskip("textual")

from textual.widgets import Input, Static  # noqa: E402

from scutch.tui import ScutchApp  # noqa: E402
from scutch.workspace import load_workspace  # noqa: E402


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    import shutil
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    for name in ("operations", "providers", "specs", "fixtures"):
        src = repo / name
        if src.is_dir():
            shutil.copytree(src, tmp_path / name)
    for name in ("scutch.toml",):
        shutil.copy(repo / name, tmp_path / name)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SCUTCH_FIXTURES", str(tmp_path / "fixtures"))
    return load_workspace(tmp_path)


def text_of(app) -> str:
    """All visible Static text, with markup stripped.

    Reading `renderable` gives the source markup; this asserts on what a person
    would actually see.
    """
    from textual.widgets import Static as _S

    parts = []
    for widget in app.query(_S):
        try:
            parts.append(widget.render().plain)
        except Exception:  # noqa: BLE001
            parts.append(str(getattr(widget, "renderable", "")))
    return "\n".join(parts)


def test_home_explains_where_you_are(ws):
    async def go():
        app = ScutchApp(ws)
        async with app.run_test() as pilot:
            await pilot.pause()
            body = text_of(app)
            assert "Scutch" in body
            assert "catalogue of operations" in body

    import asyncio

    asyncio.run(go())


def test_operation_screen_states_the_effect_and_fields(ws):
    async def go():
        app = ScutchApp(ws, operation="orders.fulfil")
        async with app.run_test() as pilot:
            await pilot.pause()
            body = text_of(app)
            assert "approval" in body.lower()
            assert set(app.inputs) == {"order_id"}

    import asyncio

    asyncio.run(go())


def test_mutating_operation_previews_before_changing_anything(ws):
    """Fill the form, press the button, and land on a summary — not a mutation."""

    async def go():
        app = ScutchApp(ws, operation="orders.fulfil")
        async with app.run_test() as pilot:
            await pilot.pause()
            app.inputs["order_id"].value = "ord_a1"
            await pilot.pause()
            app.submit()
            await pilot.pause()
            body = text_of(app)
            assert "will change" in body.lower()
            assert "Nothing has been changed yet" in body
            assert app.last_envelope["status"] in ("needs_approval", "complete")

    import asyncio

    asyncio.run(go())


def test_a_read_only_operation_says_so(ws):
    async def go():
        app = ScutchApp(ws, operation="orders.list")
        async with app.run_test() as pilot:
            await pilot.pause()
            body = text_of(app)
            assert "Reads only" in body

    import asyncio

    asyncio.run(go())


def test_workflow_screen_lists_steps_in_order(ws):
    from pathlib import Path

    feature = ws.root / "specs" / "orders" / "approval.feature"

    async def go():
        app = ScutchApp(ws, feature=feature)
        async with app.run_test() as pilot:
            await pilot.pause()
            body = text_of(app)
            assert "Steps" in body
            assert "stops" in body.lower() or "stopping" in body.lower()

    import asyncio

    asyncio.run(go())


def test_running_a_workflow_in_the_tui_reports_step_by_step(ws):
    """The workflow screen runs the same scenarios the CLI runs, and says where it stopped."""
    feature = ws.root / "specs" / "orders" / "approval.feature"

    async def go():
        app = ScutchApp(ws, feature=feature)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.run_workflow()
            await pilot.pause()
            return text_of(app)

    import asyncio

    body = asyncio.run(go())
    assert "Finished" in body or "Stopped" in body
