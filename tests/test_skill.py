"""The shipped agent skill must not drift from the CLI.

A skill is documentation an agent trusts. If it names a command that does not
exist, or omits one that does, it teaches the wrong protocol. These tests make
the skill executable truth too — same standard as `describe`.

Also checks the skill is installable by `npx skills`: `skills/<name>/SKILL.md`
with `name`/`description` frontmatter and the name matching the folder.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SKILL = REPO / "skills" / "scutch" / "SKILL.md"
REFERENCE = REPO / "skills" / "scutch" / "references" / "envelopes.md"


def frontmatter(text: str) -> dict[str, str]:
    assert text.startswith("---\n"), "SKILL.md must start with YAML frontmatter"
    block = text.split("---", 2)[1]
    out: dict[str, str] = {}
    for line in block.splitlines():
        m = re.match(r"^([a-z_]+):\s*(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip().strip('"')
    return out


def test_frontmatter_shape():
    fm = frontmatter(SKILL.read_text())
    assert fm["name"] == "scutch" == SKILL.parent.name
    assert 20 < len(fm["description"]) < 600
    # third person, trigger-oriented — skills are selected on this text
    assert not fm["description"].startswith("I ")


def test_reference_file_is_linked():
    assert REFERENCE.is_file()
    assert "references/envelopes.md" in SKILL.read_text()


def test_every_cli_verb_in_the_skill_exists():
    """Any `scutch <verb>` the skill mentions must be a real command."""
    help_out = subprocess.run(
        ["scutch", "--help"], capture_output=True, text=True, timeout=60
    )
    real = set(re.findall(r"^│ (\w+)", help_out.stdout, re.M))
    assert real, help_out.stdout[:400]

    text = SKILL.read_text() + REFERENCE.read_text()
    unknown = set()
    for line in text.splitlines():
        for verb in re.findall(r"\bscutch ([a-z]+)\b", line):
            if verb in real:
                continue
            # A mention is allowed only when the same sentence says it does not
            # exist ("There is no `scutch continue`"). Otherwise it is a promise.
            lowered = line.lower()
            if any(neg in lowered for neg in ("no `scutch", "there is no", "never", "none")):
                continue
            unknown.add(verb)
    assert not unknown, f"skill promises non-existent commands: {sorted(unknown)}"
    assert "run" in real and "cancel" in real


def test_skill_documents_every_transition_the_runner_emits():
    text = SKILL.read_text()
    from scutch.contract import ALL_STATUSES

    for status in ALL_STATUSES:
        assert status in text, f"skill omits status {status!r}"


def test_skill_does_not_promise_a_session_token():
    text = (SKILL.read_text() + REFERENCE.read_text()).lower()
    # It may (and does) say there is none; it must not offer one.
    for phrase in ("scutch continue", "session token to resume", "--session-id"):
        assert phrase not in text or "no " in text or "none" in text


def test_skill_forbids_credential_requests():
    text = SKILL.read_text()
    assert "Never request credential values" in text or "never request credential" in text.lower()
    assert "--approve-plan" in text


@pytest.mark.skipif(
    not __import__("shutil").which("npx"), reason="npx not available"
)
def test_installable_by_npx_skills(tmp_path):
    """End-to-end: the published repo installs and lands the reference files."""
    repo = subprocess.run(
        ["git", "remote", "get-url", "origin"], cwd=REPO, capture_output=True, text=True
    ).stdout.strip()
    if not repo:
        pytest.skip("no origin remote")
    proc = subprocess.run(
        ["npx", "--yes", "skills@latest", "add", repo, "--agent", "codex"],
        cwd=tmp_path, capture_output=True, text=True, timeout=300,
        env={**__import__("os").environ, "TERM": "dumb", "CI": "1"},
    )
    assert proc.returncode == 0, proc.stdout[-1500:] + proc.stderr[-800:]
    installed = tmp_path / ".agents" / "skills" / "scutch"
    assert (installed / "SKILL.md").is_file()
    assert (installed / "references" / "envelopes.md").is_file()
    assert frontmatter((installed / "SKILL.md").read_text())["name"] == "scutch"
