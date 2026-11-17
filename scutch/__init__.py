"""Scutch: a folder of plain functions, one seam to the world.

The whole interface:

    from scutch import IO, run, discover

    env = run(fn, root=ws, inputs={"order_id": "ord_a1"})          # plan
    env = run(fn, root=ws, inputs={...}, approve=env["digest"])    # apply
"""

from .catalogue import Operation, UnknownOperation, discover, get
from .io import IO, ScutchError, Unanswered, Unapproved, UnknownEffect, digest, key
from .run import apply, plan, run

__all__ = [
    "IO",
    "Operation",
    "ScutchError",
    "Unanswered",
    "Unapproved",
    "UnknownEffect",
    "UnknownOperation",
    "apply",
    "digest",
    "discover",
    "get",
    "key",
    "plan",
    "run",
]
