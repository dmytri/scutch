"""Execute every Gherkin scenario in specs/ against the real CLI."""

from __future__ import annotations

from pathlib import Path

import pytest

from .gherkin import LIBRARY, Steps, execute, parse_feature

SPECS = Path(__file__).resolve().parents[1] / "specs"


def scenarios():
    out = []
    for path in sorted(SPECS.glob("*.feature")):
        for sc in parse_feature(path):
            out.append(pytest.param(sc, id=f"{path.stem}::{sc.name}"))
    return out


@pytest.mark.parametrize("scenario", scenarios())
def test_gherkin(scenario, cli, workspace):
    execute(scenario, Steps(cli), LIBRARY)
