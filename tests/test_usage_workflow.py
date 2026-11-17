"""The real example: Metabase orders × Vercel usage -> a usage report.

This is what a workflow looks like here — an operation that calls operations —
and these tests assert the parts that matter: the composition is visible, the
plan is stable across runs, and the numbers are the numbers.
"""
import json
from pathlib import Path

import pytest

pytest.importorskip("statsmodels")

from scutch import IO, digest  # noqa: E402
from scutch.catalogue import get  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
ORDERS = json.loads((REPO / "fixtures" / "metabase" / "orders_acme.json").read_text())["rows"]
USAGE = json.loads((REPO / "fixtures" / "vercel" / "usage_acme.json").read_text())["rows"]


def answers(session="sess_x"):
    return [
        json.dumps({"id": session}),          # exec: metabase session
        json.dumps({"rows": ORDERS}),         # http: metabase card
        json.dumps({"rows": USAGE}),          # http: vercel usage
    ]


def run_report(**kwargs):
    io = IO(root=REPO, answers=answers())
    return get(REPO, "usage.analyse").fn(io, customer="acme", project="acme-app"), io


def test_the_workflow_is_visible_in_the_catalogue():
    described = get(REPO, "usage.analyse").describe()
    assert described["calls"] == ["usage.metabase", "usage.vercel"]


def test_it_asks_the_apis_through_the_same_seam():
    _, io = run_report()
    kinds = [json.loads(a)["kind"] for a in io.asks]
    assert kinds[0] == "run"            # metabase, composed
    assert kinds[1:3] == ["exec", "http"]   # inside metabase
    assert kinds[-1] == "http"          # vercel


def test_the_numbers_a_commercial_conversation_needs():
    report, _ = run_report()
    assert report["weeks"] == 78
    assert report["ttm"]["orders"] > 0
    assert report["ttm"]["requests"] > 0
    for window in ("wow", "mom", "yoy"):
        assert report[window] is not None, window
        assert set(report[window]) == {"orders", "requests"}


def test_the_forecast_covers_week_month_and_year():
    report, _ = run_report()
    forecast = report["forecast"]
    assert forecast["next_week"]["week_start"]
    assert forecast["next_month"]["orders"] > 0
    assert forecast["next_twelve_months"]["orders"] > forecast["next_month"]["orders"]
    low, high = forecast["confidence_80_next_week"]["orders"]
    assert low <= forecast["next_week"]["orders"] <= high


def test_a_week_present_in_one_series_only_is_reported_not_zero_filled():
    io = IO(root=REPO, answers=[
        json.dumps({"id": "s"}),
        json.dumps({"rows": ORDERS[:-1]}),     # one order week missing
        json.dumps({"rows": USAGE}),
    ])
    report = get(REPO, "usage.analyse").fn(io, customer="acme", project="acme-app")
    assert report["unmatched_weeks"] == 1
    assert report["weeks"] == 77


def test_the_plan_is_the_same_regardless_of_the_session_token():
    """The bug this design had: a token in the plan made the digest unstable."""

    def planned(session):
        io = IO(root=REPO, answers=answers(session), execute=False)
        get(REPO, "usage.analyse").fn(io, customer="acme", project="acme-app")
        return digest(io.effects), io.effects

    first, effects = planned("sess_ONE")
    second, _ = planned("sess_TWO")
    assert first == second
    assert not any("sess_" in effect for effect in effects)


def test_the_plan_covers_the_http_calls_and_not_the_shell_command():
    """`exec` observes; `http` may change something remote, so it is planned."""
    io = IO(root=REPO, answers=answers(), execute=False)
    get(REPO, "usage.analyse").fn(io, customer="acme", project="acme-app")
    assert [json.loads(e)["kind"] for e in io.effects] == ["http", "http"]
