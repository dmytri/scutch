"""The human interface: the same flow an agent follows, in a terminal.

An agent does: list -> describe -> run (dry) -> run --approve <digest>.
This does exactly that, in that order, with prompts instead of JSON:

    pick an operation -> fill its parameters -> read the effects -> approve

It calls the same ``plan`` and ``apply`` functions the CLI calls, so approving
here is approving the same digest. There is no second implementation and no
state kept between screens.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from . import catalogue
from .world import ScutchError, Unanswered, Unapproved
from .run import apply as apply_plan
from .run import plan as make_plan

BANNER = "Scutch — pick an operation, fill it in, approve the effects."


def effects_in_words(effects: list[str]) -> list[str]:
    """The plan as plain text.

    The browser's `effect_lines` is the same projection with colour markup; this
    strips it, so there is one description of an effect rather than two that can
    drift.
    """
    import re

    from .browser import ICON, effect_lines

    icon_classes = set(ICON.values())
    out = []
    for line in effect_lines(effects):
        plain = re.sub(r"\[[^\]]*\]", "", line)
        for icon in icon_classes:
            plain = plain.replace(icon + " ", "")
        out.append(plain.strip())
    return out

def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    return input(f"  {prompt}{suffix}: ").strip() or (default or "")


def choose(operations) -> str | None:
    """Number the catalogue and read a choice. Returns a name or None to quit."""
    names = sorted(operations)
    print(f"\n{BANNER}\n")
    width = max((len(n) for n in names), default=0)
    for index, name in enumerate(names, 1):
        doc = (operations[name].describe()["doc"] or "").splitlines()[0]
        print(f"  {index}  {name:<{width}}  {doc}")
    print("  q  quit\n")
    answer = input("choose> ").strip().lower()
    if answer in ("q", "quit", ""):
        return None
    if answer.isdigit() and 1 <= int(answer) <= len(names):
        return names[int(answer) - 1]
    if answer in operations:
        return answer
    print(f"  not a choice: {answer!r}")
    return ""


def gather(operation) -> dict[str, Any]:
    """Prompt for each parameter the signature declares."""
    print(f"\n{operation.name} — {operation.describe()['doc']}\n")
    inputs: dict[str, Any] = {}
    for parameter in operation.parameters:
        default = None if parameter["default"] is None else str(parameter["default"])
        if not parameter["required"] and default is None:
            value = ask(f"{parameter['name']} (optional, blank to skip)")
            if value:
                inputs[parameter["name"]] = value
            continue
        value = ask(parameter["name"] + (" (required)" if parameter["required"] else ""), default)
        if not value and parameter["required"]:
            print("  a value is needed.")
            return {}
        if value:
            inputs[parameter["name"]] = value
    return inputs


def show_plan(env: dict[str, Any]) -> list[str]:
    print("\n  This will:")
    for line in effects_in_words(env["effects"]):
        print(f"    {line}")
    print(f"\n  digest {env['digest']}")
    return env["effects"]


def one(operation, root: Path) -> None:
    """One full pass: fill, plan, approve, apply."""
    inputs = gather(operation)
    if not inputs and operation.parameters:
        return
    env = make_plan(operation.fn, root=root, inputs=inputs)
    show_plan(env)
    if not env["effects"]:
        print("\n  Nothing to approve — it is already in that state.\n")
        return
    if input("\n  approve? [y/N]> ").strip().lower() not in ("y", "yes"):
        print("  not applied. Nothing was changed.\n")
        return
    try:
        out = apply_plan(operation.fn, root=root, inputs=inputs, approve=env["digest"])
    except Unapproved as exc:
        print(f"\n  refused: {exc}\n")
        return
    print("\n  applied")
    for key, value in (out["result"] or {}).items():
        print(f"    {key:<10} {value}")
    print()


def main(root: Path | None = None) -> int:
    """The loop. Ctrl-C or an empty choice leaves without changing anything."""
    root = Path.cwd() if root is None else Path(root)
    operations = catalogue.discover(root)
    if not operations:
        print(f"no operations under {root / 'operations'}")
        return 1
    try:
        while True:
            name = choose(operations)
            if name is None:
                print("bye")
                return 0
            if name:
                one(operations[name], root)
    except (KeyboardInterrupt, EOFError):
        print("\nbye")
        return 0
    except (ScutchError, Unanswered) as exc:
        print(f"\n{exc}")
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
