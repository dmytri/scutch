"""The window arithmetic lives in a .sql file a person can run."""
import json
import shlex
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SQL = REPO / "operations" / "usage" / "queries" / "windows.sql"
ORDERS = REPO / "fixtures" / "metabase" / "orders_acme.json"


def run_sql(rows):
    script = f"SET VARIABLE orders={shlex.quote(json.dumps(rows))};{SQL.read_text()}"
    done = subprocess.run(["duckdb", "-json", "-c", script], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)[0]


def test_the_query_is_a_file_that_runs_standalone():
    assert SQL.is_file()
    assert SQL.suffix == ".sql"


def test_it_computes_the_windows_the_report_uses():
    rows = json.loads(ORDERS.read_text())["rows"]
    got = run_sql(rows)
    assert got["months"] == len(rows)
    assert got["latest_month"] == rows[-1]["month"]
    assert got["latest_orders"] == rows[-1]["orders"]
    assert got["ttm_orders"]
    # MoM is this month against the previous one, by construction.
    expected = round((rows[-1]["orders"] / rows[-2]["orders"] - 1) * 100, 2)
    assert got["mom_pct"] == expected


def test_a_short_series_reports_nulls_rather_than_inventing_history():
    got = run_sql([{"month": "2026-01", "orders": 10}])
    assert got["mom_pct"] is None
    assert got["yoy_pct"] is None
    assert got["ttm_orders"] in (10, "10")
