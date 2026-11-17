"""The CLI: `list`, `describe`, `run`.

JSON on stdout, diagnostics on stderr, and no prompt ever. An agent reads an
envelope; a person reads the same envelope. There is no second interface to keep
in step.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from . import catalogue
from .io import ScutchError, Unanswered, Unapproved
from .run import run as run_operation

USAGE = """scutch list
scutch describe OPERATION
scutch run OPERATION [--input JSON | --input @FILE] [--approve DIGEST]
scutch run OPERATION --input - < input.json
scutch ui

Envelopes are JSON on stdout. A plan has a status of "dry"; repeat the same
command with --approve DIGEST to apply exactly that plan."""


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path.cwd()
    try:
        _ensure_ledger(root)
        if not args or args[0] in ("-h", "--help", "help"):
            print(USAGE)
            return 0
        if args[0] == "list":
            return _list(root)
        if args[0] == "describe":
            return _describe(root, args[1:])
        if args[0] == "run":
            return _run(root, args[1:])
        if args[0] == "ui":
            from .tui import main as ui_main

            return ui_main(root)
        return _fail(f"unknown command {args[0]!r}", 2)
    except (ScutchError, Unapproved, Unanswered) as exc:
        return _fail(str(exc), 1)


def _ensure_ledger(root: Path) -> None:
    """Copy the seed to the working ledger once, if it is not there yet.

    The seed is never written to, so a demo is repeatable: delete ledger.json and
    the next run starts from the committed state.
    """
    seed = Path(__file__).parent / "data" / "seed.json"
    working = root / "ledger.json"
    if not working.is_file() and seed.is_file():
        working.write_text(seed.read_text())


def _list(root: Path) -> int:
    operations = catalogue.discover(root)
    _emit(
        {
            "operations": [
                {"name": op.name, "doc": op.describe()["doc"].splitlines()[0] if op.describe()["doc"] else ""}
                for op in operations.values()
            ]
        }
    )
    return 0


def _describe(root: Path, args: list[str]) -> int:
    if not args:
        return _fail("describe needs an operation name", 2)
    return _emit(catalogue.get(root, args[0]).describe())


def _run(root: Path, args: list[str]) -> int:
    if not args:
        return _fail("run needs an operation name", 2)
    name, rest = args[0], args[1:]
    inputs = _inputs(rest)
    approve = _flag(rest, "--approve")
    op = catalogue.get(root, name)
    env = run_operation(op.fn, root=root, inputs=inputs, approve=approve)
    _emit({"operation": name, **env})
    return 0


def _inputs(args: list[str]) -> dict[str, Any]:
    raw = _flag(args, "--input")
    if raw is None:
        return {}
    if raw == "-":
        text = sys.stdin.read()
    elif raw.startswith("@"):
        text = Path(raw[1:]).read_text()
    else:
        text = raw
    try:
        value = json.loads(text or "{}")
    except json.JSONDecodeError as exc:
        raise ScutchError(f"--input is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ScutchError("--input must be a JSON object")
    return value


def _flag(args: list[str], name: str) -> str | None:
    if name in args:
        index = args.index(name)
        if index + 1 >= len(args):
            raise ScutchError(f"{name} needs a value")
        return args[index + 1]
    return None


def _emit(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0


def _fail(message: str, code: int) -> int:
    print(json.dumps({"error": {"message": message}}, indent=2), file=sys.stdout)
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
