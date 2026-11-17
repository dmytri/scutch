"""One lifecycle, two interfaces.

The TUI must not be a second implementation: the same inputs must produce the
same status, the same plan digest, and approve the same thing. If these ever
diverge, the human is approving something the agent would not.
"""

from __future__ import annotations

import pytest

pytest.importorskip("textual")

from textual.widgets import Input  # noqa: E402

from scutch import human  # noqa: E402
from scutch.runner import execute  # noqa: E402
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
    shutil.copy(repo / "scutch.toml", tmp_path / "scutch.toml")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SCUTCH_FIXTURES", str(tmp_path / "fixtures"))
    return load_workspace(tmp_path)


INPUT = {"customer_id": "cust_456", "permission": "read_orders"}


def test_cli_and_tui_agree_the_plan_digest_approves_the_same_thing(ws):
    cli_env, _ = execute(ws, "saleor.customer.repair", dict(INPUT), fmt="json")

    async def go():
        app = ScutchApp(ws, operation="saleor.customer.repair")
        async with app.run_test() as pilot:
            await pilot.pause()
            app.inputs["customer_id"].value = "cust_456"
            app.inputs["permission"].value = "read_orders"
            await pilot.pause()
            app.submit()
            await pilot.pause()
            return app.last_envelope

    import asyncio

    tui_env = asyncio.run(go())
    assert tui_env["status"] == cli_env["status"]
    tui_plan = (tui_env.get("plan") or {}).get("digest")
    cli_plan = (cli_env.get("plan") or {}).get("digest")
    assert tui_plan == cli_plan, "the human would approve a different plan than the agent"


def test_approving_in_the_tui_is_approve_plan_digest(ws):
    cli_preview, _ = execute(ws, "saleor.customer.repair", dict(INPUT), fmt="json")
    cli_applied, _ = execute(
        ws,
        "saleor.customer.repair",
        dict(INPUT),
        approve_plan=(cli_preview.get("plan") or {}).get("digest"),
        fmt="json",
    )

    async def go():
        app = ScutchApp(ws, operation="saleor.customer.repair")
        async with app.run_test() as pilot:
            await pilot.pause()
            app.inputs["customer_id"].value = "cust_456"
            app.inputs["permission"].value = "read_orders"
            await pilot.pause()
            app.submit()
            await pilot.pause()
            app.approve()
            await pilot.pause()
            return app.last_envelope

    import asyncio

    tui_env = asyncio.run(go())
    assert tui_env["status"] == cli_applied["status"]
    assert (tui_env.get("result") or {}) == (cli_applied.get("result") or {})


def test_human_and_agent_see_the_same_facts(ws):
    """The phrasing differs; the underlying data must not."""
    env, _ = execute(ws, "saleor.customer.repair", dict(INPUT), fmt="json")
    person = human.outcome(env)
    assert person["status"] == env["status"]
    assert person["redacted"] == (env.get("redacted") or [])
    assert person["label"] in human.STATUS_LABEL.values()


def test_an_unknown_error_still_gets_readable_text():
    what, todo = human.error_guide({"code": "something_new", "message": "the cable is unplugged"})
    assert what == "the cable is unplugged"
    assert todo == ""


def test_plan_sentences_read_change_kinds_without_guessing():
    lines = human.plan_sentences({"changes": [{"kind": "permission.add", "value": "read_orders"}]})
    assert lines == ["Add the permission “read_orders”."]
    unusual = human.plan_sentences({"changes": [{"kind": "reticulate", "value": 3}]})
    assert "reticulate" in unusual[0] and "3" in unusual[0]
    assert human.plan_sentences({"changes": []}) == ["Nothing will change — it is already in that state."]
