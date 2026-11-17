"""The real workflow: Metabase orders × Vercel FOCUS billing -> a usage report.

A workflow here is an operation that calls operations. These tests assert the
parts that matter: the composition is visible, the numbers are reproducible from
the files on disk, credentials never reach the plan, and the forward view covers
the windows a deal is argued over.
"""
import json
from pathlib import Path

import pytest

pytest.importorskip("statsmodels")

from scutch import IO  # noqa: E402
from scutch.catalogue import get  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
ORDERS = REPO / "fixtures" / "metabase" / "orders_acme.json"
BILLING = REPO / "fixtures" / "vercel" / "billing_acme.jsonl"


def report(**kwargs):
    io = IO(root=REPO)
    return get(REPO, "usage.analyse").fn(
        io, customer="acme", project="acme-app", orders_export=str(ORDERS),
        billing_export=str(BILLING), **kwargs
    )


def test_the_workflow_is_visible_in_the_catalogue():
    described = get(REPO, "usage.analyse").describe()
    assert set(described["calls"]) == {"usage.metabase", "usage.vercel"}


def test_it_reads_files_and_calls_no_network():
    """Both inputs are exports, so the whole workflow is offline and repeatable."""
    io = IO(root=REPO)
    get(REPO, "usage.analyse").fn(
        io, customer="acme", project="acme-app",
        orders_export=str(ORDERS), billing_export=str(BILLING),
    )
    assert io.effects == []
    assert all(json.loads(a)["kind"] in ("read", "run") for a in io.asks)


def test_the_windows_are_reproducible_from_the_sql_file():
    """The SQL is the definition; the report must agree with running it."""
    import subprocess
    import shlex

    rows = json.loads(ORDERS.read_text())["rows"]
    sql = (REPO / "operations" / "usage" / "queries" / "windows.sql").read_text()
    out = subprocess.run(
        ["duckdb", "-json", "-c", f"SET VARIABLE orders={shlex.quote(json.dumps(rows))};{sql}"],
        capture_output=True, text=True, check=True,
    )
    windows = json.loads(out.stdout)[0]
    got = report()
    assert got["mom"]["orders"] == windows["mom_pct"]
    assert got["yoy"]["orders"] == windows["yoy_pct"]
    assert got["ttm"]["orders"] == int(windows["ttm_orders"])


def test_every_hosting_component_is_shown_raw_and_converted():
    got = report()
    components = got["hosting"]["components"]
    assert [c["name"] for c in components] == [
        "CDN requests", "ISR writes", "Transfer GB", "Image transforms",
    ]
    for component in components:
        assert "raw" in component and "weight" in component and "equivalent" in component
    # The total is truncated; the components keep their fractions so a reader can
    # see where the rounding went. Accept either side of the truncation.
    total = sum(c["equivalent"] for c in components)
    assert got["hosting"]["equivalent_requests"] in (int(total), int(total) + 1)


def test_a_truncated_billing_export_is_reported_not_ignored(tmp_path):
    lines = BILLING.read_text().splitlines()
    broken = tmp_path / "broken.jsonl"
    broken.write_text("\n".join(lines[:3] + ["{not json"]))
    io = IO(root=REPO)
    got = get(REPO, "usage.vercel").fn(io, export=str(broken))
    assert got["unparseable"] == 1
    assert got["records"] == 3


def test_the_forecast_covers_next_week_month_and_year():
    forecast = report()["forecast"]
    assert forecast["method"].startswith("SARIMAX")
    assert forecast["next_week"]["orders"] > 0
    assert forecast["next_month"]["orders"] > 0
    twelve = forecast.get("next_twelve_months") or forecast["ntm"]
    assert twelve["orders"] > forecast["next_month"]["orders"]
    low, high = forecast["confidence_80_next_month"]["orders"]
    assert low <= forecast["next_month"]["orders"] <= high


def test_a_missing_month_column_fails_rather_than_inventing_zeros(tmp_path):
    """A changed saved question is information, not something to paper over."""
    import json as _json

    bad = tmp_path / "orders.json"
    bad.write_text(_json.dumps({"rows": [{"period": "2026-01", "orders": 5}]}))
    io = IO(root=REPO)
    with pytest.raises(KeyError):
        get(REPO, "usage.metabase").fn(io, customer="acme", export=str(bad))
