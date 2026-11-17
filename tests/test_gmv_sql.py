"""The GMV bases are DuckDB files, and the operation is the guardrail around them.

The point of rule 1: the arithmetic is a `.sql` artifact a human can run, and
the operation adds schema, refusal and a result — nothing else.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
QUERIES = REPO / "operations" / "bi"

needs_duckdb = pytest.mark.skipif(
    shutil.which("duckdb") is None, reason="duckdb not installed"
)


def test_every_basis_has_a_query_file():
    for name in ("net_confirmed", "charged", "charged_plus_offplatform", "api_derived"):
        assert (QUERIES / f"{name}.sql").is_file(), name


@needs_duckdb
def test_query_files_run_standalone():
    """A query must be runnable without Scutch — that is what makes it checkable."""
    for name in ("net_confirmed", "charged", "charged_plus_offplatform", "api_derived"):
        sql = (QUERIES / f"{name}.sql").read_text()
        setup = (
            "SET VARIABLE net=1; SET VARIABLE charged=1; SET VARIABLE confirmation=1;"
            " SET VARIABLE return_rate=0; SET VARIABLE off_platform_share=0;"
            " SET VARIABLE external_payment_uplift=1; SET VARIABLE requests=1;"
            " SET VARIABLE requests_per_dollar=1;"
        )
        out = subprocess.run(
            ["duckdb", "-no-agent", "-csv", "-c", setup + sql],
            capture_output=True, text=True, check=False,
        )
        assert out.returncode == 0, f"{name}: {out.stderr[:200]}"
        assert out.stdout.strip(), f"{name} returned nothing"


def test_net_confirmed_reproduces_the_stored_report(workspace, cli):
    """Verified exact: the Dreamtonics account-economics report's ourGmv."""
    env, code, err = cli.scutch(
        "run", "saleor.gmv.compute", "--input", "@-", "--format", "json",
        stdin={
            "basis": "net-confirmed",
            "net": 2967349.6045842674,
            "confirmation": 0.8850600770800272,
            "return_rate": 0.15,
        },
    )
    assert code == 0, err
    assert env["result"]["our_gmv"] == 2232340.27
    assert env["result"]["query"] == "operations/bi/net_confirmed.sql"


def test_a_missing_input_for_the_chosen_basis_is_named(workspace, cli):
    env, _, _ = cli.scutch(
        "run", "saleor.gmv.compute", "--input", "@-", "--format", "json",
        stdin={"basis": "api-derived"},
    )
    assert env["status"] == "failed"
    assert "requests" in env["error"]["message"]
    assert "api-derived" in env["error"]["message"]


def test_an_unknown_basis_lists_the_permitted_values(workspace, cli):
    env, _, _ = cli.scutch(
        "run", "saleor.gmv.compute", "--input", "@-", "--format", "json",
        stdin={"basis": "made-up"},
    )
    assert env["status"] == "failed"
    joined = str(env["error"]["details"])
    for basis in ("net-confirmed", "charged", "api-derived"):
        assert basis in joined


def test_bases_operation_states_what_each_requires(workspace, cli):
    env, code, err = cli.scutch("run", "saleor.gmv.bases", "--input", "@-", "--format", "json", stdin={})
    assert code == 0, err
    bases = {b["basis"]: b for b in env["result"]["bases"]}
    assert set(bases) == {"net-confirmed", "charged", "charged-plus-offplatform", "api-derived"}
    assert bases["api-derived"]["requires"] == ["requests", "requests_per_dollar"]
    assert env["result"]["default"] == "net-confirmed"
