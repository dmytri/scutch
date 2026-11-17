"""The CLI is the interface. These run it as a subprocess, from a fresh dir."""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def scutch(cwd: Path, *args: str, stdin: str | None = None):
    """Run the CLI with `cwd` as the workspace.

    The workspace is the directory holding `operations/`, so these tests run
    against a temp copy of the repo's operations with its own ledger.
    """
    done = subprocess.run(
        [sys.executable, "-m", "scutch.cli", *args],
        cwd=cwd, capture_output=True, text=True,
        env={"PYTHONPATH": str(REPO), "PATH": "/usr/bin:/bin"},
        input=stdin,
    )
    return done.returncode, json.loads(done.stdout or "{}"), done.stderr


def workspace(tmp_path: Path) -> Path:
    """A workspace: the repo's operations, a fresh ledger."""
    import shutil

    shutil.copytree(REPO / "operations", tmp_path / "operations")
    (tmp_path / "ledger.json").write_text((REPO / "scutch" / "data" / "seed.json").read_text())
    return tmp_path


def test_list_names_the_operations(tmp_path):
    code, out, _ = scutch(workspace(tmp_path), "list")
    assert code == 0
    names = [o["name"] for o in out["operations"]]
    assert set(names) == {"orders.fulfil", "orders.fulfil_all", "orders.list", "orders.notify"}


def test_describe_reports_the_parameters(tmp_path):
    code, out, _ = scutch(workspace(tmp_path), "describe", "orders.fulfil")
    assert code == 0
    assert [p["name"] for p in out["parameters"]] == ["order_id"]


def test_run_plans_then_applies(tmp_path):
    ws = workspace(tmp_path)
    code, plan, _ = scutch(ws, "run", "orders.fulfil", "--input", '{"order_id":"ord_a1"}')
    assert code == 0 and plan["status"] == "dry"
    assert json.loads((ws / "ledger.json").read_text())["orders"][0]["status"] == "placed"

    code, applied, _ = scutch(
        ws, "run", "orders.fulfil",
        "--input", '{"order_id":"ord_a1"}', "--approve", plan["digest"],
    )
    assert code == 0 and applied["status"] == "applied"
    assert json.loads((ws / "ledger.json").read_text())["orders"][0]["status"] == "fulfilled"


def test_input_from_stdin(tmp_path):
    code, out, _ = scutch(workspace(tmp_path), "run", "orders.list", "--input", "-",
                          stdin='{"status":"placed"}')
    assert code == 0
    assert out["result"]["count"] == 2


def test_an_unknown_operation_is_an_error_not_a_traceback(tmp_path):
    code, out, err = scutch(workspace(tmp_path), "run", "nope.nothing", "--input", "{}")
    assert code == 1
    assert "no operation" in out["error"]["message"]
    assert "Traceback" not in err
