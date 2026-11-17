"""The docs must not name commands that do not exist.

An agent reads these; a stale command is a wasted turn at best and a wrong
workflow at worst. This is the same drift check the skill already has, extended
to README and docs/.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DOCS = [REPO / "README.md", *sorted((REPO / "docs").glob("*.md")), REPO / "skills" / "scutch" / "SKILL.md"]


def real_commands() -> set[str]:
    from scutch.cli import app

    return {c.name or c.callback.__name__ for c in app.registered_commands}


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_docs_only_name_real_commands(path):
    known = real_commands()
    text = path.read_text()
    unknown = set()
    # Command position only: start of a line, inside a code span/block, or after
    # `$`. This keeps prose like "from scutch import" out of the check.
    for match in re.finditer(r"(?:^|\$ |\s`)scutch ([a-z]+)", text, re.M):
        verb = match.group(1)
        if verb in known:
            continue
        # a mention is fine only when the document says it does not exist
        if f"no `scutch {verb}`" in text or f"there is no `scutch {verb}`" in text:
            continue
        unknown.add(verb)
    assert not unknown, f"{path.name} names non-existent commands: {sorted(unknown)}"


def test_the_four_conventions_are_declared():
    import tomllib

    cfg = tomllib.loads((REPO / "scutch.toml").read_text())
    conv = cfg.get("conventions", {})
    assert conv["queries"] == "duckdb"
    assert conv["steps"] == "poe"
    assert conv["secrets"] == "provider"
    assert conv.get("feature_is_workflow") is True


def test_context_publishes_conventions_and_the_workflow_command(workspace, cli):
    env, code, err = cli.scutch("context", "--format", "json")
    assert code == 0, err
    assert env["conventions"]["steps"] == "poe"
    assert "scutch workflow" in env["bootstrap"]
    assert "scutch doctor" in env["bootstrap"]
