"""A small Gherkin engine: parse features, dispatch steps to a step library.

Deliberately minimal. It owns the *format* (Feature/Scenario/Given-When-Then) and
the dispatch contract (every step must resolve to a method, or the run fails);
it owns nothing about any particular repository's steps.

Two consumers so far: Scutch's own suite and saleor-bi's operations. They share
the parser and the "no undefined steps" rule, and supply their own step methods.
Putting the engine in the package (rather than in one repo's tests/) is what lets
a workspace treat its feature files as executable without copying a runner.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

STEP_KEYWORDS = ("Given", "When", "Then", "And", "But")
_STEP_RE = re.compile(r"^(Given|When|Then|And|But)\b")


@dataclass
class Scenario:
    feature: str
    name: str
    steps: list[tuple[str, str]] = field(default_factory=list)


def parse_feature(path: Path | str) -> list[Scenario]:
    """Parse one feature file. Steps outside a scenario are an error."""
    path = Path(path)
    scenarios: list[Scenario] = []
    feature_name = path.stem
    current: Scenario | None = None
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if line.startswith("Feature:"):
            feature_name = line.split(":", 1)[1].strip()
        elif line.startswith("Scenario:"):
            current = Scenario(feature_name, line.split(":", 1)[1].strip())
            scenarios.append(current)
        elif _STEP_RE.match(line):
            if current is None:
                raise ValueError(f"{path}: step outside a scenario: {line}")
            keyword, text = line.split(" ", 1)
            current.steps.append((keyword, text.strip()))
    return scenarios


def discover(directory: Path | str) -> list[Scenario]:
    out: list[Scenario] = []
    for path in sorted(Path(directory).glob("*.feature")):
        out.extend(parse_feature(path))
    return out


class UndefinedStep(Exception):
    """A scenario used a step no library implements."""


@dataclass
class Step:
    pattern: re.Pattern[str]
    method: str
    #: Extra args extracted from the step text beyond the pattern groups.
    extract: Callable[[str], Any] | None = None


def step(pattern: str, method: str, extract: Callable[[str], Any] | None = None) -> Step:
    return Step(re.compile(f"^{pattern}$"), method, extract)


def quoted(text: str) -> tuple[str, ...]:
    """All double-quoted fragments, in order."""
    return tuple(re.findall(r'"([^"]+)"', text))


def named_values(text: str) -> tuple[tuple[str, str], ...]:
    """`id "x", channel "y" and permission "z"` -> (("id","x"), ...)."""
    names = re.findall(r'(\w+) "', text)
    values = re.findall(r'"([^"]+)"', text)
    if len(names) != len(values):
        raise UndefinedStep(f"cannot pair names with values in {text!r}")
    return tuple(zip(names, values))


def numbers(text: str) -> tuple[float, ...]:
    """Every numeric literal, in order."""
    return tuple(float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", text))


def execute(scenario: Scenario, steps: Any, library: Sequence[Step]) -> None:
    """Run one scenario against ``steps`` using ``library`` for dispatch.

    An undefined step raises immediately: a scenario that cannot be executed is
    not documentation, it is a false claim.
    """
    for keyword, text in scenario.steps:
        for entry in library:
            match = entry.pattern.match(text)
            if not match:
                continue
            method = getattr(steps, entry.method)
            if entry.extract is not None:
                method(*entry.extract(text))
            else:
                method(*match.groups())
            break
        else:
            raise UndefinedStep(f"undefined step in {scenario.name!r}: {keyword} {text}")


def coverage(scenarios: Iterable[Scenario], library: Sequence[Step]) -> dict[str, list[str]]:
    """Steps no library entry matches — a pre-flight check before running."""
    unresolved: dict[str, list[str]] = {}
    for scenario in scenarios:
        for keyword, text in scenario.steps:
            if not any(entry.pattern.match(text) for entry in library):
                unresolved.setdefault(scenario.name, []).append(f"{keyword} {text}")
    return unresolved
