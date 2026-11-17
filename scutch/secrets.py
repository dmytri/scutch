"""Secrets are names, never values.

An operation asks for a secret by *name*. Where the value comes from is decided
here, in one place, and never by the operation:

    env:FOO            environment variable FOO
    cmd:pass show x    the stdout of a command
    file:path          the contents of a file

Two rules make this worth having rather than a wrapper around `os.environ`:

1. A secret never becomes a plan. It is not an effect, so it is not hashed,
   printed, or shown on the review screen.
2. A missing secret fails with the *name* it looked for, so the fix is obvious
   and the value is never in an error message.

The value is only ever in memory, only inside the call that needed it.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from .errors import ScutchError


class MissingSecret(ScutchError):
    """A secret was referenced but not provided. The name is safe; the value is not."""

    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.hint = hint


def resolve(reference: str) -> str:
    """Turn `env:NAME` / `cmd:...` / `file:...` into a value.

    Anything else is treated as an environment variable name, because the most
    common case should be the shortest to write.
    """
    kind, _, target = reference.partition(":")

    if not _:
        kind, target = "env", reference

    if kind == "env":
        value = os.environ.get(target)
        if value is None:
            raise MissingSecret(
                f"secret {target} is not set",
                hint=f"export {target}=... (or store it in `pass` and use cmd:pass show {target})",
            )
        return value

    if kind == "cmd":
        done = subprocess.run(target, shell=True, capture_output=True, text=True)
        if done.returncode != 0:
            # Note: stderr is *not* included. It can contain the secret.
            raise MissingSecret(
                f"secret command failed: {target!r} exited {done.returncode}",
                hint="check the command outside scutch; its stderr is not shown "
                "because it can contain the secret",
            )
        return done.stdout.strip()

    if kind == "file":
        path = Path(target)
        if not path.is_file():
            raise MissingSecret(f"secret file {target} does not exist")
        return path.read_text().strip()

    raise MissingSecret(f"unknown secret reference {reference!r}")


class Vault:
    """Resolves secret references for one run, with a cache.

    Caching matters: a workflow may need the same token for several steps, and
    shelling out to `pass` once per call would be slow and could rotate mid-run.
    """

    def __init__(self, values: dict[str, str] | None = None):
        self._cache: dict[str, str] = dict(values or {})

    def __call__(self, reference: str) -> str:
        if reference not in self._cache:
            self._cache[reference] = resolve(reference)
        return self._cache[reference]

    def __repr__(self) -> str:  # pragma: no cover - defensive
        # Never let a repr of the vault print a value.
        return f"Vault({len(self._cache)} secret(s))"


def redact(text: str, secret: str) -> str:
    """Remove a secret from a string before it can be stored or shown."""
    return text.replace(secret, "<redacted>") if secret else text
