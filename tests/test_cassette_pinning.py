"""A scenario must read its own recording, not a merged directory.

Five cassettes share the request `query Customer($id)` for different ids, and
three of them answer for the same id (`cust_456`). Merged, the provider picks by
file order — so without a pin a scenario can pass on another scenario's data.

This test would fail before `LocalHarness._cassette_pinned`: the in-process
harness kept `recording` but never applied it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from scutch import gherkin
from scutch.harness import LocalHarness
from scutch.steps import LIBRARY, Steps
from scutch.workspace import load_workspace

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    for name in ("operations", "providers", "specs", "fixtures"):
        src = REPO / name
        if src.is_dir():
            shutil.copytree(src, tmp_path / name)
    shutil.copy(REPO / "scutch.toml", tmp_path / "scutch.toml")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SCUTCH_FIXTURES", str(tmp_path / "fixtures"))
    monkeypatch.delenv("SCUTCH_REPLAY", raising=False)
    return load_workspace(tmp_path)


def run(feature: Path, ws):
    harness = LocalHarness(ws)
    return gherkin.run_feature(gherkin.parse_feature(feature), Steps(harness), LIBRARY)


def test_the_harness_pins_the_recording_during_a_command(ws, monkeypatch):
    import os

    harness = LocalHarness(ws)
    harness.recording = "scenario-repair"
    seen = {}

    def capture():
        seen["during"] = os.environ.get("SCUTCH_REPLAY")
        return {}, 0, ""

    harness._dispatch = lambda *a, **k: capture()
    harness.run(["scutch", "list"])
    assert seen["during"] == "scenario-repair"
    assert os.environ.get("SCUTCH_REPLAY") is None, "the pin must not leak to the next command"


def test_the_pin_is_restored_when_it_was_already_set(ws, monkeypatch):
    import os

    monkeypatch.setenv("SCUTCH_REPLAY", "outer")
    harness = LocalHarness(ws)
    harness.recording = "scenario-repair"
    harness._dispatch = lambda *a, **k: ({}, 0, "")
    harness.run(["scutch", "list"])
    assert os.environ["SCUTCH_REPLAY"] == "outer"


def test_a_scenario_runs_against_its_own_cassette(ws):
    feature = ws.root / "specs" / "saleor" / "customer_repair.feature"
    result = run(feature, ws)
    assert result.ok, f"stopped at {result.failure.scenario}: {result.failure.error}"


def test_pointing_a_scenario_at_the_wrong_cassette_fails(ws, tmp_path):
    """The failure mode the pin prevents: data borrowed from another scenario."""
    source = (ws.root / "specs" / "saleor" / "customer_repair.feature").read_text()
    # Ask for cust_456 with the `nochange` recording, which only knows cust_123.
    confused = tmp_path / "confused.feature"
    confused.write_text(source.replace('recording "scenario-repair"', 'recording "scenario-nochange"'))

    scenarios = [
        s for s in gherkin.parse_feature(confused)
        if "missing permission" in s.name
    ]
    assert scenarios, "expected the repair scenario"
    outcome = gherkin.run_feature(scenarios, Steps(LocalHarness(ws)), LIBRARY)
    assert not outcome.ok, "a scenario reading the wrong cassette must not pass"
    assert outcome.failure is not None
