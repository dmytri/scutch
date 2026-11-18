"""Secrets are names, never values.

An operation asks for a secret by *name*. Where the value comes from is decided
here, in one place, and never by the operation:

    env:FOO            environment variable FOO
    FOO                the same, because it is the common case

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
    """Turn `env:NAME` (or a bare `NAME`) into a value.

    Only the environment is read. `cmd:` and `file:` were built before anything
    asked for them; when an operation needs one, that is the moment to add it,
    and the test that proves it is worth writing then.
    """
    name = reference.removeprefix("env:")
    value = os.environ.get(name)
    if value is None:
        raise MissingSecret(
            f"secret {name} is not set",
            hint=f"export {name}=...",
        )
    return value


class Vault:
    """Resolves secret references for one run.

    `values` lets a test or a caller supply one directly, which is the only way
    to run a secret-bearing operation without touching the environment.
    """

    def __init__(self, values: dict[str, str] | None = None):
        self._values = dict(values or {})

    def __call__(self, reference: str) -> str:
        return self._values.get(reference) or resolve(reference)

    def __repr__(self) -> str:  # pragma: no cover - defensive
        # Never let a repr of the vault print a value.
        return f"Vault({len(self._values)} secret(s))"

