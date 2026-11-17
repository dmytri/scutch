"""One lifecycle, two interfaces.

The TUI must not be a second implementation: the same inputs must produce the
same status, the same plan digest, and approve the same thing. If these ever
diverge, the human is approving something the agent would not.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytest.importorskip("textual")

from textual.widgets import Input  # noqa: E402

from scutch import human  # noqa: E402
from scutch.runner import execute  # noqa: E402
from scutch.tui import ScutchApp  # noqa: E402
from scutch.workspace import load_workspace  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
INPUT = {"order_id": "ord_a1"}


def make_workspace(tmp_path: Path, name: str):
    """An isolated copy of the demo workspace, so two surfaces can each start
    from the same seed ledger without seeing each other's mutations."""
    root = tmp_path / name
    for entry in ("operations", "providers", "specs", "fixtures"):
        src = REPO / entry
        if src.is_dir():
            shutil.copytree(src, root / entry)
    shutil.copy(REPO / "scutch.toml", root / "scutch.toml")
    return load_workspace(root)


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return make_workspace(tmp_path, "agent")


def _run_tui(ws, *, approve: bool):
    """Drive the TUI through the guided form and return its final envelope."""

    async def go():
        app = ScutchApp(ws, operation="orders.fulfil")
        async with app.run_test() as pilot:
            await pilot.pause()
            app.inputs["order_id"].value = "ord_a1"
            await pilot.pause()
            app.submit()
            await pilot.pause()
            if approve:
                app.approve()
                await pilot.pause()
            return app.last_envelope

    import asyncio

    return asyncio.run(go())


def test_cli_and_tui_agree_the_plan_digest_approves_the_same_thing(ws):
    cli_env, _ = execute(ws, "orders.fulfil", dict(INPUT), fmt="json")
    tui_env = _run_tui(ws, approve=False)

    assert tui_env["status"] == cli_env["status"] == "needs_approval"
    tui_plan = (tui_env.get("plan") or {}).get("digest")
    cli_plan = (cli_env.get("plan") or {}).get("digest")
    assert tui_plan == cli_plan, "the human would approve a different plan than the agent"


def test_approving_in_the_tui_is_approve_plan_digest(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Each surface gets its own seeded workspace, so neither sees the other's
    # applied mutation. On identical inputs they must agree exactly.
    agent_ws = make_workspace(tmp_path, "agent")
    human_ws = make_workspace(tmp_path, "human")

    cli_preview, _ = execute(agent_ws, "orders.fulfil", dict(INPUT), fmt="json")
    cli_applied, _ = execute(
        agent_ws,
        "orders.fulfil",
        dict(INPUT),
        approve_plan=(cli_preview.get("plan") or {}).get("digest"),
        fmt="json",
    )

    tui_env = _run_tui(human_ws, approve=True)
    assert tui_env["status"] == cli_applied["status"] == "complete"
    assert (tui_env.get("result") or {}) == (cli_applied.get("result") or {})


def test_human_and_agent_see_the_same_facts(ws):
    """The phrasing differs; the underlying data must not."""
    env, _ = execute(ws, "orders.fulfil", dict(INPUT), fmt="json")
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
