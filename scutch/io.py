"""One seam. Every effect goes through ``io(kind, **args)``.

kinds: ``read``, ``write``, ``exec``, ``http``. Nothing else reaches the world.

There are two ideas, and only two:

``answers``
    Canned replies, consumed in order. Present means this run is a **double**.
    A reply is a permission slip, not a fact: it exists so the operation can walk
    to its next ask. Nothing asserts what a reply contains.

``execute``
    ``False`` means writes do not happen. A dry run uses this to learn what the
    operation *would* do without doing it.

An operation is an ordinary function taking ``io`` first. It is the whole
contract: the plan is the effects it asked for, and approval binds to that.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

KINDS = ("read", "write", "exec", "http")
#: Kinds that leave a mark. Every one of them belongs in a plan.
EFFECTS = ("write", "exec", "http")


class ScutchError(Exception):
    """Base for the four failures a caller can act on."""


class Unanswered(ScutchError):
    """The double was asked more often than it was given answers."""


class Unapproved(ScutchError):
    """The effects about to happen are not the effects that were approved."""


class UnknownEffect(ScutchError):
    """An ``io`` kind outside the four."""


def key(kind: str, args: dict[str, Any]) -> str:
    """The canonical form of one ask. This is what gets compared and hashed."""
    return json.dumps({"kind": kind, **args}, sort_keys=True, separators=(",", ":"))


def digest(effects: list[str]) -> str:
    """A plan is its effects: same effects, same digest; any change, new digest."""
    return "sha256:" + hashlib.sha256("\n".join(effects).encode()).hexdigest()


@dataclasses.dataclass
class IO:
    """The single door to the world."""

    root: Path
    #: Canned replies, consumed in order. None means live.
    answers: list[Any] | None = None
    #: Whether writes, exec and http may happen.
    execute: bool = True
    calls: list[tuple[str, dict[str, Any]]] = dataclasses.field(default_factory=list)

    def __post_init__(self) -> None:
        self.root = Path(self.root)

    def __call__(self, kind: str, **args: Any) -> Any:
        if kind not in KINDS:
            raise UnknownEffect(f"{kind!r} is not one of {KINDS}")
        # Every call is recorded first and by one path, so no branch can slip
        # past the double: a write that returned early would touch the disk in a
        # run that claimed to be doubled.
        self.calls.append((kind, args))

        if kind == "write":
            # A write asks for nothing and answers nothing, so it consumes no
            # canned reply. Only kinds that return a value do.
            if self.execute:
                path = self.root / args["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(args["text"])
            return None

        if self.answers is not None:
            return self._reply()

        if kind == "read":
            return (self.root / args["path"]).read_text()
        if not self.execute:
            return ""
        if kind == "exec":
            return subprocess.run(args["argv"], capture_output=True, text=True).stdout
        request = urllib.request.Request(
            args["url"],
            data=args.get("body", "").encode() or None,
            method=args.get("method", "GET"),
        )
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
            return response.read().decode()

    def _reply(self) -> Any:
        assert self.answers is not None
        if not self.answers:
            raise Unanswered(key(*self.calls[-1]))
        return self.answers.pop(0)

    # -- what a caller asserts on ------------------------------------------

    @property
    def asks(self) -> list[str]:
        """Every call, in order. This is the claim: it asked this, then this."""
        return [key(kind, args) for kind, args in self.calls]

    @property
    def effects(self) -> list[str]:
        """The subset that leaves a mark: what a plan has to cover."""
        return [key(kind, args) for kind, args in self.calls if kind in EFFECTS]
