"""A tiny order ledger, kept in a JSON file.

This is the demo's storage, and it is deliberately dull: nothing here should
compete for attention with what is being demonstrated — that an operation
declares its contract, plans before it mutates, and resumes from an envelope.

It is a *provider* in Scutch's sense: operations never open the file, they are
handed a handle the runtime resolved for them.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from scutch.errors import NotFound, ProviderError


LEDGER_DEFAULT = "fixtures/orders.json"
#: Where a working copy lives. The committed file is a seed, never mutated: a
#: feature run must be repeatable, and a demo must not dirty its own fixture.
WORKING_DEFAULT = ".scutch/ledger.json"


class Ledger:
    """Orders as one JSON document, read and written whole."""

    def __init__(self, seed: Path, working: Path):
        self.seed = seed
        self.working = working

    # -- infrastructure ---------------------------------------------------

    def _ensure(self) -> None:
        """Copy the seed into the working file once, if it is not there yet."""
        if self.working.is_file():
            return
        if not self.seed.is_file():
            raise ProviderError(
                f"no ledger seed at {self.seed}",
                hint="commit an orders.json, or set data in scutch.toml",
            )
        self.working.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.seed, self.working)

    def reset(self) -> None:
        """Discard the working copy so the next read starts from the seed."""
        self.working.unlink(missing_ok=True)

    def _read(self) -> dict[str, Any]:
        self._ensure()
        try:
            return json.loads(self.working.read_text())
        except json.JSONDecodeError as exc:
            raise ProviderError(
                f"ledger at {self.working} is not valid JSON",
                details={"error": str(exc)},
            ) from exc

    def _write(self, data: dict[str, Any]) -> None:
        self.working.parent.mkdir(parents=True, exist_ok=True)
        self.working.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")

    # -- reads ------------------------------------------------------------

    def all_orders(self) -> list[dict[str, Any]]:
        return list(self._read().get("orders", []))

    def get(self, order_id: str) -> dict[str, Any]:
        for order in self.all_orders():
            if order.get("id") == order_id:
                return order
        raise NotFound(
            f"order {order_id!r} is not in the ledger",
            hint="run orders.list to find a valid id",
        )

    def outstanding(self) -> list[dict[str, Any]]:
        """Orders placed but not yet fulfilled, oldest first."""
        rows = [o for o in self.all_orders() if o.get("status") == "placed"]
        return sorted(rows, key=lambda o: (o.get("placed_at", ""), o.get("id", "")))

    # -- writes -----------------------------------------------------------

    def set_status(self, order_id: str, status: str) -> dict[str, Any]:
        data = self._read()
        for order in data.get("orders", []):
            if order.get("id") == order_id:
                before = order.get("status")
                order["status"] = status
                self._write(data)
                return {"id": order_id, "before": before, "after": status}
        raise NotFound(f"order {order_id!r} is not in the ledger")


# --------------------------------------------------------------------------
# The factory the runtime calls. `scutch.toml` declares mode = "local", so there
# is no secret to resolve and no cassette to replay: this "API" is a file.


def make(resolution) -> Ledger:
    root = resolution.root
    assert root is not None, "a ledger needs a workspace root"
    seed = root / (resolution.config.data or LEDGER_DEFAULT)
    return Ledger(seed, root / WORKING_DEFAULT)
