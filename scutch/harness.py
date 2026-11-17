"""An in-process harness for `scutch run <feature>`.

`scutch.steps.Steps` needs three things from a harness: run a command, drain the
request log, and reset the workspace. The test suite supplies one that shells out
to the CLI; this one calls the runner directly, so executing a feature needs no
subprocess and no pytest.

Same steps, same features, two harnesses — which is what makes a feature the
workflow *and* its proof.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .errors import ScutchError


class LocalHarness:
    """Runs `scutch …` against one workspace, in this process."""

    def __init__(self, ws, *, pristine: Path | None = None):
        self.ws = ws
        self.workspace = ws.root
        self.recording: str | None = None
        self._pristine = pristine or (ws.root / ".scutch" / "pristine")

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

        from . import cli as cli_mod

        try:
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
        except ScutchError as exc:
            return {"kind": "scutch.error/v1", "error": exc.to_json()}, exc.exit_code, ""

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
        """Restore the workspace to its pristine snapshot, if one exists."""
        if not self._pristine.is_dir():
            # No snapshot: clear only the run store, which is what a reset means
            # when the workspace is the live one.
            runs = self.ws.runs_dir
            if runs.is_dir():
                for f in runs.glob("run_*.json"):
                    f.unlink()
            return
        for child in list(self.workspace.iterdir()):
            if child.name in ("scutch.toml", ".git"):
                continue
            shutil.rmtree(child) if child.is_dir() else child.unlink()
        shutil.copytree(self._pristine, self.workspace, dirs_exist_ok=True)

    # -- conveniences the steps use ---------------------------------------

    def scutch(self, *args: str, stdin: dict | None = None) -> tuple[dict, int, str]:
        return self.run(["scutch", *args], stdin=stdin)
