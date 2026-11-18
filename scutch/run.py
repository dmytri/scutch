"""Plan, then apply. Two passes over one ordinary function.

An operation is called twice with the same inputs:

1. **dry** — ``execute=False``. Reads happen, so the plan reflects today's
   state. Writes, exec and http do not. What it *would* have done is the plan.
2. **apply** — the same call with execution on. If its effects differ from what
   was approved, nothing is kept and the run is refused.

Because there is only one function, there is no plan to write separately and no
way for a plan to be absent from an operation that mutates. The plan is what the
function asked for; approval binds to the digest of exactly that.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .world import World, Unapproved, digest
from .secrets import Vault

Operation = Callable[..., Any]


def plan(op: Operation, *, root: Path | str, inputs: dict[str, Any], answers=None) -> dict[str, Any]:
    """What would this do? Reads are real; effects are only recorded."""
    world = World(root=root, answers=answers, execute=False, vault=Vault())
    result = op(world, **inputs)
    effects = world.effects
    return {
        "status": "dry",
        "result": result,
        "effects": effects,
        "digest": digest(effects),
    }


def apply(
    op: Operation,
    *,
    root: Path | str,
    inputs: dict[str, Any],
    approve: str,
    answers=None,
) -> dict[str, Any]:
    """Do it, if the effects are exactly the ones approved."""
    planned = plan(op, root=root, inputs=inputs, answers=answers)
    if approve != planned["digest"]:
        raise Unapproved(
            f"approved {approve}, but this run would do {planned['digest']}"
        )

    world = World(root=root, answers=answers, execute=True, vault=Vault())
    result = op(world, **inputs)
    if digest(world.effects) != planned["digest"]:
        # The world moved between the dry pass and the apply pass. Refuse, and
        # say so: a run that silently applied a different plan is the bug this
        # whole design exists to prevent.
        raise Unapproved("the effects changed between planning and applying")
    return {
        "status": "applied",
        "result": result,
        "effects": world.effects,
        "digest": planned["digest"],
    }


def run(
    op: Operation,
    *,
    root: Path | str,
    inputs: dict[str, Any],
    approve: str | None = None,
    answers=None,
) -> dict[str, Any]:
    """Plan when no approval is given, apply when it is. One entry point."""
    if approve is None:
        return plan(op, root=root, inputs=inputs, answers=answers)
    return apply(op, root=root, inputs=inputs, approve=approve, answers=answers)
