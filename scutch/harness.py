"""An in-process harness for `scutch run <feature>`.

`scutch.steps.Steps` needs three things from a harness: run a command, drain the
request log, and reset the workspace. The test suite supplies one that shells out
to the CLI; this one calls the runner directly, so executing a feature needs no
subprocess and no pytest.

Same steps, same features, two harnesses — which is what makes a feature the
workflow *and* its proof.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .errors import ScutchError


class LocalHarness:
    """Runs `scutch …` against one workspace, in this process."""

    def __init__(self, ws, *, pristine: Path | None = None):
        self.ws = ws
        self.workspace = ws.root
        self.recording: str | None = None
        # The snapshot must live *outside* the workspace. A snapshot inside it
        # is deleted by the very reset that needs it — a self-destruct in which
        # the restore silently becomes a no-op and one scenario's mutation leaks
        # into the next.
        self._pristine = pristine or (Path(tempfile.mkdtemp(prefix="scutch-pristine-")) / "ws")
        self._data_dirs = ("fixtures",)
        self._snapshot()

    def _snapshot(self) -> None:
        """Record only what a run may write to, before any scenario can.

        `fixtures` is mutable data; operations, providers and specs are inputs
        and are never restored over — restoring them would undo an edit made
        during the run for no reason.
        """
        if self._pristine.is_dir():
            shutil.rmtree(self._pristine)
        self._pristine.mkdir(parents=True, exist_ok=True)
        for name in self._data_dirs:
            src = self.workspace / name
            if src.is_dir():
                shutil.copytree(
                    src,
                    self._pristine / name,
                    ignore=lambda d, names: {n for n in names if n == "__pycache__"},
                    dirs_exist_ok=True,
                )

    # -- the harness contract ---------------------------------------------

    def run(self, argv: list[str], stdin: dict | None = None) -> tuple[dict, int, str]:
        """Execute one `scutch` argv. Returns (envelope, exit code, stderr)."""
        if not argv or argv[0] != "scutch":
            return {"kind": "scutch.error/v1", "error": {"code": "usage_error",
                    "message": f"harness only runs `scutch …`, got {argv[:1]}"}}, 2, ""

        verb = argv[1] if len(argv) > 1 else ""
        args = argv[2:]
        fmt = "json"
        if "--format" in args:
            fmt = args[args.index("--format") + 1]

        with self._cassette_pinned():
            try:
                return self._dispatch(verb, args, stdin, fmt)
            except ScutchError as exc:
                return {"kind": "scutch.error/v1", "error": exc.to_json()}, exc.exit_code, ""

    @contextlib.contextmanager
    def _cassette_pinned(self):
        """Pin the scenario's cassette for the duration of one command.

        Without this the provider merges every recording in the directory, so a
        request key present in several cassettes is answered by whichever file
        sorts first: a scenario can pass while reading another scenario's data.
        """
        if self.recording is None:
            yield
            return
        previous = os.environ.get("SCUTCH_REPLAY")
        os.environ["SCUTCH_REPLAY"] = self.recording
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop("SCUTCH_REPLAY", None)
            else:
                os.environ["SCUTCH_REPLAY"] = previous

    def _dispatch(self, verb: str, args: list[str], stdin: dict | None, fmt: str):
        if verb == "run":
            from .runner import execute

            op = next((a for a in args if not a.startswith("-") and a not in ("@-",)), None)
            if op is None:
                return {"kind": "scutch.error/v1", "error": {"code": "usage_error",
                        "message": "run needs an operation name"}}, 2, ""
            approve = args[args.index("--approve-plan") + 1] if "--approve-plan" in args else None
            idem = args[args.index("--idempotency-key") + 1] if "--idempotency-key" in args else None
            env, code = execute(
                self.ws, op, stdin or {}, approve_plan=approve, idempotency_key=idem, fmt=fmt
            )
            return env, code, ""
        if verb == "status":
            from .runner import status

            payload, code = status(self.ws, args[0], fmt)
            return payload, code, ""
        if verb == "cancel":
            from .runner import cancel

            payload, code = cancel(self.ws, args[0], fmt)
            return payload, code, ""
        if verb == "context":
            return self.ws.context(), 0, ""
        if verb == "describe":
            from .discovery import describe

            return describe(self.ws, args[0]), 0, ""
        if verb == "list":
            from .discovery import list_ops

            return list_ops(self.ws), 0, ""
        if verb == "doctor":
            from .discovery import doctor

            return doctor(self.ws), 0, ""
        return {"kind": "scutch.error/v1", "error": {"code": "not_implemented",
                "message": f"the in-process harness does not implement {verb!r}"}}, 70, ""

    def drain_wire(self) -> list[dict]:
        """Transport requests since the last drain, then clear the sink.

        Draining without clearing would make every later assertion count the
        whole run — "exactly one mutation" would pass on the second scenario
        because the first scenario's mutation was still in the log.
        """
        from .providers import clear_wire, wire_log

        rows = wire_log(self.ws.root)
        clear_wire(self.ws.root)
        return rows

    def reset(self) -> None:
        """Return the mutable data and the run store to their starting state.

        Two things are reset, and nothing else is touched:

        * the data directories are replaced from the snapshot;
        * the run store and any working copies are cleared, so this scenario
          cannot inherit an applied record and replay instead of apply.

        Operations, providers and specs are left alone: they are inputs to the
        run, not state produced by it.
        """
        for name in getattr(self, "_data_dirs", ("fixtures",)):
            live = self.workspace / name
            saved = self._pristine / name
            if live.is_dir():
                shutil.rmtree(live)
            if saved.is_dir():
                shutil.copytree(saved, live)

        runs = self.ws.runs_dir
        if runs.is_dir():
            for f in runs.glob("run_*.json"):
                f.unlink()

        # A provider may keep a working copy of its own (the ledger does). It is
        # derived from the data above, so it must go with it.
        for extra in (self.workspace / ".scutch" / "ledger.json",):
            extra.unlink(missing_ok=True)

    # -- conveniences the steps use ---------------------------------------

    def scutch(self, *args: str, stdin: dict | None = None) -> tuple[dict, int, str]:
        return self.run(["scutch", *args], stdin=stdin)
