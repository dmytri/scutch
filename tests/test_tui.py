"""The TUI is a projection: same calls, same digest, same refusal.

These drive the prompts with a canned stdin, so the flow a person walks is the
flow under test — not a reimplementation of it.
"""
import io as _io
import json

import pytest

from scutch import tui
from scutch.run import plan


class Feeds:
    """Replace builtins.input with a script of answers."""

    def __init__(self, answers):
        self.answers = list(answers)

    def __call__(self, prompt=""):
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)


@pytest.fixture()
def feed(monkeypatch):
    def install(answers):
        monkeypatch.setattr("builtins.input", Feeds(answers))

    return install


def test_the_choice_screen_lists_every_operation(ws, ops, capsys, feed):
    feed(["q"])
    tui.main(ws)
    out = capsys.readouterr().out
    for name in ops:
        assert name in out


def test_it_plans_then_applies_the_same_digest_the_cli_computes(ws, ops, capsys, feed):
    """The claim: approving here approves what the agent would approve."""
    expected = plan(ops["orders.fulfil"].fn, root=ws, inputs={"order_id": "ord_a1"})
    feed(["1", "ord_a1", "y", "q"])
    tui.main(ws)
    out = capsys.readouterr().out
    assert expected["digest"] in out
    assert json.loads((ws / "ledger.json").read_text())["orders"][0]["status"] == "fulfilled"


def test_saying_no_changes_nothing(ws, ops, capsys, feed):
    feed(["1", "ord_a1", "n", "q"])
    tui.main(ws)
    assert "not applied" in capsys.readouterr().out
    assert json.loads((ws / "ledger.json").read_text())["orders"][0]["status"] == "placed"


def test_it_shows_the_effects_in_words(ws, ops, capsys, feed):
    feed(["1", "ord_a1", "n", "q"])
    tui.main(ws)
    out = capsys.readouterr().out
    assert "write ledger.json" in out


def test_a_read_only_operation_says_there_is_nothing_to_approve(ws, ops, capsys, feed):
    feed(["3", "q"])
    tui.main(ws)
    out = capsys.readouterr().out
    assert "already in that state" in out


def test_effects_in_words_reads_each_kind():
    effects = [
        json.dumps({"kind": "write", "path": "a.json", "text": "x"}),
        json.dumps({"kind": "exec", "argv": ["ls", "-l"]}),
        json.dumps({"kind": "http", "url": "https://x/y", "method": "POST", "body": ""}),
    ]
    assert tui.effects_in_words(effects) == ["write a.json", "run ls -l", "POST https://x/y"]


def test_effects_in_words_states_the_truth_when_there_is_nothing():
    assert tui.effects_in_words([]) == ["nothing — it is already in that state."]
