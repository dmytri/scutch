"""One seam. Every effect goes through ``world(kind, **args)``.

kinds: ``read``, ``write``, ``exec``, ``http``. Nothing else reaches the world.

There are two ideas, and only two:

``answers``
    Canned replies, consumed in order. Present means this run is a **double**.
    A reply is a permission slip, not a fact: it exists so the operation can walk
    to its next ask. Nothing asserts what a reply contains.

``execute``
    ``False`` means writes do not happen. A dry run uses this to learn what the
    operation *would* do without doing it.

An operation is an ordinary function taking ``world`` first. It is the whole
contract: the plan is the effects it asked for, and approval binds to that.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .errors import Failed, Missing, ScutchError, Unanswered, Unapproved, UnknownEffect
from .secrets import MissingSecret

__all__ = [
    "Failed", "World", "KINDS", "EFFECTS", "Missing", "MissingSecret", "OPAQUE",
    "ScutchError", "Unanswered", "Unapproved", "UnknownEffect", "digest", "key",
]

KINDS = ("read", "write", "exec", "http", "run", "secret")
#: Kinds that change the world, and therefore belong in a plan.
#:
#: `write` changes a file. `http` leaves the process and may change something
#: remote. `exec` is *not* here: it is an observation. A command's answer is
#: data — a session token, a hostname, a list of rows — and putting that in the
#: plan has two bad consequences this module used to have: a credential gets
#: printed and hashed (so it lands in the approval digest), and the same plan
#: hashes differently every run because the token changed.
#:
#: `run` is absent for the same reason as `write`'s opposite: calling another
#: operation is not itself an effect. The inner operation reports its own, into
#: this same seam, so a workflow's blast radius is the sum of its steps.
EFFECTS = ("write", "http")

#: Arguments that carry a credential. They are replaced with whether they were
#: present, never with their value, before an ask is hashed or shown.
OPAQUE = ("headers", "password", "token", "secret", "api_key")


def key(kind: str, args: dict[str, Any]) -> str:
    """The canonical form of one ask. This is what gets compared and hashed.

    Credential-bearing arguments are reduced to whether they were present, not
    what they were: a session token in a header would otherwise be printed in
    the plan and hashed into the digest, which is where the last bug came from.
    """
    shown = {
        name: ("<redacted>" if name in OPAQUE and value else value)
        for name, value in args.items()
    }
    return json.dumps({"kind": kind, **shown}, sort_keys=True, separators=(",", ":"))


def digest(effects: list[str]) -> str:
    """A plan is its effects: same effects, same digest; any change, new digest."""
    return "sha256:" + hashlib.sha256("\n".join(effects).encode()).hexdigest()


@dataclasses.dataclass
class World:
    """The single door to the world."""

    root: Path
    #: Canned replies, consumed in order. None means live.
    answers: list[Any] | None = None
    #: Whether writes, exec and http may happen.
    execute: bool = True
    #: Resolves `env:NAME` / `cmd:...` / `file:...` to a value. Injected so a
    #: test never needs a real credential, and so a dry run resolves nothing.
    vault: Any = None
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

        if kind == "secret":
            # A secret is never an effect: it is not in EFFECTS, so it cannot
            # reach the plan, the digest or the review screen. It is also not
            # resolved during a dry run — a plan should not need credentials.
            if not self.execute:
                return "<unresolved>"
            if self.vault is None:
                raise MissingSecret(
                    f"secret {args.get('name')!r} was requested but no vault is configured"
                )
            return self.vault(args["name"])

        if kind == "run":
            # Call another operation through this same seam. It inherits the
            # mode, so a dry run stays dry all the way down and a doubled run
            # stays doubled — there is no way to escape by composing.
            from .catalogue import get

            operation = get(self.root, args["operation"])
            nested = {k: v for k, v in args.items() if k != "operation"}
            return operation.fn(self, **nested)

        if self.answers is not None:
            return self._reply()

        if kind == "read":
            path = self.root / args["path"]
            try:
                return path.read_text()
            except FileNotFoundError:
                raise Missing(f"no file {args['path']} in {self.root}") from None
        if not self.execute:
            return ""
        if kind == "exec":
            done = subprocess.run(args["argv"], capture_output=True, text=True)
            if done.returncode != 0:
                raise Failed(
                    f"{' '.join(args['argv'])} exited {done.returncode}"
                    + (f": {done.stderr.strip()[:200]}" if done.stderr.strip() else "")
                )
            return done.stdout
        request = urllib.request.Request(
            args["url"],
            data=args.get("body", "").encode() or None,
            method=args.get("method", "GET"),
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
                return response.read().decode()
        except urllib.error.HTTPError as exc:
            raise Failed(f"{args['url']} answered {exc.code}") from None
        except urllib.error.URLError as exc:
            raise Failed(f"cannot reach {args['url']}: {exc.reason}") from None

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
        """What would change the world: what a plan has to cover.

        Deliberately excludes `read`, `exec` and `run`. Those *observe*; their
        answers are data and may contain anything, including a credential. A
        plan that included them would be unreproducible and would leak.
        """
        return [key(kind, args) for kind, args in self.calls if kind in EFFECTS]

    @property
    def observations(self) -> list[str]:
        """What the run asked the world, including calls whose *answers* were
        fed into later calls. Not part of the plan; useful in a failure report."""
        return [key(kind, args) for kind, args in self.calls if kind not in EFFECTS]
