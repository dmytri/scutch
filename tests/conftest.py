"""Every test gets a workspace: just a ledger, seeded from the committed one."""
from __future__ import annotations

from pathlib import Path

import pytest

SEED = Path(__file__).resolve().parents[1] / "scutch" / "data" / "seed.json"


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    """A workspace: the repo's operations, its own ledger."""
    import shutil

    repo = Path(__file__).resolve().parents[1]
    shutil.copytree(repo / "operations", tmp_path / "operations")
    (tmp_path / "ledger.json").write_text(SEED.read_text())
    return tmp_path


@pytest.fixture()
def ops():
    """The demo operations, loaded from the repo, not from the workspace."""
    from scutch import discover

    return discover(Path(__file__).resolve().parents[1])


def ledger(ws: Path) -> dict[str, str]:
    import json

    data = json.loads((ws / "ledger.json").read_text())
    return {o["id"]: o["status"] for o in data["orders"]}
