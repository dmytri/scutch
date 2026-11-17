"""Test harness for the shared step vocabulary.

The steps themselves live in ``scutch.steps`` because `scutch workflow` executes
them too. This module only supplies the *test* harness: a `Cli` that shells out
to the real command, so a feature proves the CLI surface and not just the
library. The in-process harness (`scutch.steps.InProcessHarness`) is what
`scutch workflow` uses.

Nothing step-related belongs here — a duplicated vocabulary is a vocabulary that
drifts.
"""

from __future__ import annotations

from scutch.gherkin import (  # re-exported for tests
    Scenario,
    UndefinedStep,
    coverage,
    discover,
    execute,
    named_values,
    numbers,
    parse_feature,
    quoted,
    step,
)
from scutch.steps import LIBRARY, Steps

__all__ = [
    "LIBRARY",
    "Steps",
    "Scenario",
    "UndefinedStep",
    "coverage",
    "discover",
    "execute",
    "named_values",
    "numbers",
    "parse_feature",
    "quoted",
    "step",
]
