"""Discover operations: a folder of plain functions.

An operation is a module in ``operations/`` holding a function whose first
parameter is ``io``. The module's dotted path *is* the operation name, so
``operations/orders/fulfil.py`` is ``orders.fulfil``.

There is no registry, no decorator and no directory of metadata. The folder is
the catalogue; the signature is the schema. Adding an operation means adding a
file, which is the smallest possible amount of ceremony for the thing we
actually want: a fresh session can list what exists and read how to call it.
"""

from __future__ import annotations

import importlib.util
import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .io import ScutchError


class UnknownOperation(ScutchError):
    """No operation by that name."""


@dataclass(frozen=True)
class Operation:
    name: str
    fn: Callable[..., Any]
    source: Path

    @property
    def parameters(self) -> list[dict[str, Any]]:
        """The call signature, minus `io` — what a caller must supply."""
        out = []
        for parameter in inspect.signature(self.fn).parameters.values():
            if parameter.name == "io":
                continue
            out.append(
                {
                    "name": parameter.name,
                    "required": parameter.default is inspect.Parameter.empty,
                    "default": None
                    if parameter.default is inspect.Parameter.empty
                    else parameter.default,
                }
            )
        return out

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "parameters": self.parameters,
            "source": str(self.source),
            "doc": (inspect.getdoc(self.fn) or "").strip(),
        }


def discover(root: Path | str) -> dict[str, Operation]:
    """Load every operation under ``<root>/operations``."""
    base = Path(root) / "operations"
    found: dict[str, Operation] = {}
    if not base.is_dir():
        return found
    for path in sorted(base.rglob("*.py")):
        if path.name.startswith("_"):
            continue
        name = ".".join(path.relative_to(base).with_suffix("").parts)
        found[name] = Operation(name=name, fn=_load(path, name), source=path)
    return found


def get(root: Path | str, name: str) -> Operation:
    operations = discover(root)
    if name in operations:
        return operations[name]
    raise UnknownOperation(
        f"no operation {name!r}; known: {', '.join(sorted(operations)) or 'none'}"
    )


def _load(path: Path, name: str) -> Callable[..., Any]:
    module_name = "scutch_ops_" + name.replace(".", "_")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ScutchError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for _, member in inspect.getmembers(module, inspect.isfunction):
        if list(inspect.signature(member).parameters)[:1] == ["io"]:
            return member
    raise ScutchError(
        f"{path} defines no operation: expected a function whose first parameter is `io`"
    )
