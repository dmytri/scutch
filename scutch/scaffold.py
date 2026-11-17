"""Scaffolding: make the compliant shape the easy one.

Every convention in ``scutch.toml`` has a corresponding decision here, so an
author — usually an agent — fills in bodies instead of remembering rules. The
generated code is deliberately minimal: enough to pass ``scutch doctor`` and to
be obviously incomplete, so nothing looks finished that isn't.
"""

from __future__ import annotations

import re
from pathlib import Path

from .errors import ContractError, UsageError
from .workspace import Workspace

NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


def _module_path(ws: Workspace, name: str) -> Path:
    """`orders.refund.issue` -> operations/orders/refund/issue.py"""
    parts = name.split(".")
    *dirs, leaf = parts
    return ws.root / "operations" / Path(*dirs) / f"{leaf}.py"


def _operation_source(name: str, effect: str, query: str | None) -> str:
    fn = name.split(".")[-1]
    provider = name.split(".")[1] if "." in name else "service"
    consequential = effect in ("plan_apply", "mutate")

    lines = [
        f'"""`{name}` — TODO: one sentence saying what this does.',
        "",
        "Scaffolded by `scutch new`. Fill in the body, then run `scutch doctor`.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "import dataclasses",
        "from typing import Annotated",
        "",
        "from scutch import Change, Plan, Provider, field, operation, plan_for",
        "",
        f'API = Provider("{provider}")',
        "",
        "",
        "@dataclasses.dataclass",
        "class Result:",
        '    """TODO: the public result. `Annotated[..., private()]` fields are withheld."""',
        "",
        "    ok: bool = True",
        "",
        "",
        "@operation(",
        f'    name="{name}",',
        '    summary="TODO: one line for the catalogue.",',
        f'    effect="{effect}",',
        '    credentials={"api": API},',
    ]
    if query:
        lines.append(f'    query="{query}",')
        lines.append("    transforms_data=True,")
    if consequential:
        lines += [
            '    idempotency_key=["id"],',
            '    intended_effects=["TODO: the mutation this performs"],',
        ]
    lines += [
        '    examples=[',
        f'        {{"name": "basic", "why": "TODO", "input": {{"id": "x"}}}},',
        "    ],",
        ")",
        f"def {fn}(id: Annotated[str, field(description=\"TODO\")], *, api) -> Result:",
        f'    """TODO: what `{name}` does."""',
        "    raise NotImplementedError",
        "",
    ]

    if consequential:
        lines += [
            "",
            f'@plan_for("{name}")',
            f"def {fn}_plan(id: Annotated[str, field(description=\"TODO\")], *, api) -> Plan:",
            '    """The read path: what would change, as data. Empty plan means no change."""',
            "    return Plan(",
            "        changes=[",
            '            Change(kind="TODO", target=f"TODO:{id}", value=None),',
            "        ],",
            "    )",
            "",
        ]
    return "\n".join(lines)


def _query_source(name: str) -> str:
    return "\n".join(
        [
            f"-- {name}",
            "--",
            "-- A query is a DuckDB artifact before it is an operation: run it here",
            "-- by hand, then let the operation guard the inputs.",
            "--",
            "--   duckdb -c \"$(cat this-file.sql)\"",
            "",
            "SELECT",
            "    1 AS todo",
            ";",
            "",
        ]
    )


def scaffold_operation(ws: Workspace, name: str, *, effect: str, query: bool) -> list[Path]:
    if not NAME_RE.match(name):
        raise UsageError(
            f"{name!r} is not a dotted lowercase name",
            hint="e.g. orders.refund.issue",
        )
    if effect not in ("read", "plan_apply", "mutate"):
        raise UsageError(f"unknown effect {effect!r}", hint="read | plan_apply | mutate")

    module = _module_path(ws, name)
    if module.exists():
        raise ContractError(f"{module.relative_to(ws.root)} already exists")

    query_rel = None
    written: list[Path] = []
    if query:
        sql = module.with_suffix(".sql")
        query_rel = str(sql.relative_to(ws.root))
        sql.parent.mkdir(parents=True, exist_ok=True)
        sql.write_text(_query_source(name))
        written.append(sql)

    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(_operation_source(name, effect, query_rel))
    written.insert(0, module)

    # A feature is required by convention, so scaffold it too — an operation
    # with no scenario fails `doctor` immediately.
    written.extend(scaffold_feature(ws, name))
    return written


def scaffold_feature(ws: Workspace, name: str, *, domain: str | None = None) -> list[Path]:
    root = ws.features_dir
    folder = domain or name.split(".")[0]
    target = root / folder / f"{name.replace('.', '_')}.feature"
    if target.exists():
        return []

    body = "\n".join(
        [
            f"Feature: {name}",
            f"  TODO: what this workflow does to the outside world.",
            "",
            f"  Covers: {name}",
            "",
            "  Scenario: TODO name the first step",
            "    Given a fresh workspace",
            f'    When I run {name} with input {{"id": "TODO"}}',
            '    # TODO: replace with what this operation actually does, e.g.',
            '    #     Then the envelope status is "complete"',
            '    #     And the request log holds exactly 1 mutation',
            '    Then the scaffold is unfinished',
            "",
        ]
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body)
    return [target]
