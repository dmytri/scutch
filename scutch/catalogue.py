"""Discover operations: a folder of plain functions.

An operation is a module in ``operations/`` holding a function whose first
parameter is ``world``. The module's dotted path *is* the operation name, so
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

from .world import ScutchError


#: The name an operation must give its seam parameter.
#:
#: `io` would have been the obvious choice and is exactly wrong: it is a stdlib
#: module, so an operation that did `import io` would get the seam instead, and
#: the failure would be confusing rather than loud. `world` says what the object
#: is — ask the world — and shadows nothing.
WORLD_PARAM = "world"


class UnknownOperation(ScutchError):
    """No operation by that name."""


@dataclass(frozen=True)
class Operation:
    name: str
    fn: Callable[..., Any]
    source: Path

    @property
    def parameters(self) -> list[dict[str, Any]]:
        """The call signature, minus `world` — what a caller must supply."""
        out = []
        for parameter in inspect.signature(self.fn).parameters.values():
            if parameter.name == WORLD_PARAM:
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

    @property
    def calls(self) -> list[str]:
        """Operations this one composes, read from `world("run", operation=...)`.

        A workflow is an operation that calls operations. Declaring that in the
        code — rather than in a separate YAML — means the steps and the code
        cannot disagree, and the catalogue can show the graph.
        """
        import ast

        try:
            tree = ast.parse(self.source.read_text())
        except SyntaxError:
            return []
        found = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg == "operation" and isinstance(keyword.value, ast.Constant):
                    found.append(str(keyword.value.value))
        return sorted(set(found))

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "parameters": self.parameters,
            "source": str(self.source),
            "doc": (inspect.getdoc(self.fn) or "").strip(),
            "calls": self.calls,
        }


SEED = Path(__file__).parent / "data" / "seed.json"


def seed(root: Path | str) -> None:
    """Copy the demo seed to the working ledger if it is not there yet.

    Called by every entry point (CLI, browser, prompts) rather than each one
    remembering: a workspace that is missing its data should look the same
    however you opened it.
    """
    working = Path(root) / "ledger.json"
    if not working.is_file() and SEED.is_file():
        working.write_text(SEED.read_text())


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
    found = [
        member
        for _, member in inspect.getmembers(module, inspect.isfunction)
        # A leading underscore marks a helper explicitly, so it is not the
        # operation even if it takes `world`.
        if not member.__name__.startswith("_")
        and list(inspect.signature(member).parameters)[:1] == [WORLD_PARAM]
    ]
    if not found:
        raise ScutchError(
            f"{path} defines no operation: expected a function whose first parameter is `world`"
        )
    if len(found) > 1:
        # Picking one silently is how a module ends up exposing the wrong
        # operation for months. The file *is* the operation; a helper that takes
        # `world` must be named with a leading underscore or moved elsewhere.
        names = ", ".join(sorted(fn.__name__ for fn in found))
        raise ScutchError(
            f"{path} defines {len(found)} operations ({names}); "
            "one file is one operation — rename helpers with a leading underscore"
        )
    return found[0]
