"""Specs: what an operation promises, written where the runner can check it.

A spec is a small YAML file next to the code it describes. It is not a test
framework and it does not execute — the tests do that. It exists so that:

* the *intent* survives, in prose, next to the arithmetic;
* a missing spec is a failure, not an oversight (`scutch check`);
* the numbers that must not move are written down, so a change to them is a
  decision rather than a diff nobody read.

Keeping it declarative is the point. The moment a spec can do things, it becomes
a second implementation and the two disagree.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .catalogue import discover

SPEC_SUFFIX = ".spec.yaml"


class SpecError(Exception):
    """A spec is missing, malformed, or contradicts the code."""


def path_for(operation) -> Path:
    """Where an operation's spec lives: beside it, named after it."""
    return operation.source.with_suffix("").with_suffix(".spec.yaml")


def load(operation) -> dict[str, Any] | None:
    spec_path = path_for(operation)
    if not spec_path.is_file():
        return None
    try:
        data = yaml.safe_load(spec_path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise SpecError(f"{spec_path}: not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise SpecError(f"{spec_path}: a spec must be a mapping")
    return data


def check(root: Path | str) -> list[dict[str, Any]]:
    """Every operation has a spec, and every spec agrees with the code.

    Returns one row per operation. A row with `problems` non-empty is a failure;
    `scutch check` prints them and exits non-zero.
    """
    operations = discover(root)
    rows = []
    for name, operation in sorted(operations.items()):
        problems: list[str] = []
        spec = None
        try:
            spec = load(operation)
        except SpecError as exc:
            problems.append(str(exc))
        if spec is None and not problems:
            problems.append(
                f"no spec at {path_for(operation).relative_to(Path(root))}"
            )
        if spec:
            declared = spec.get("operation")
            if declared != name:
                problems.append(
                    f"spec says operation {declared!r}, the file is {name!r}"
                )
            if not spec.get("intent"):
                problems.append("spec has no `intent`")
            # Parameters the spec documents must exist in the signature, and the
            # required ones must all be documented. A spec that describes a
            # different signature is worse than no spec.
            documented = set((spec.get("parameters") or {}).keys())
            actual = {p["name"] for p in operation.describe()["parameters"]}
            unknown = documented - actual
            if unknown:
                problems.append(f"spec documents parameters that do not exist: {sorted(unknown)}")
            required = {p["name"] for p in operation.describe()["parameters"] if p["required"]}
            missing = required - documented
            if missing:
                problems.append(f"spec omits required parameters: {sorted(missing)}")
            # A workflow's spec must name the steps it composes, and they must
            # match what the code calls.
            if spec.get("steps") is not None:
                if sorted(spec["steps"]) != sorted(operation.calls):
                    problems.append(
                        f"spec steps {sorted(spec['steps'])} != code calls {sorted(operation.calls)}"
                    )
        rows.append({"operation": name, "spec": spec is not None, "problems": problems})
    return rows
