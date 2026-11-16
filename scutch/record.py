"""Record GraphQL exchanges into a replay fixture.

This is the *only* place a real endpoint is contacted, and it is a deliberate,
explicit act — `scutch record` — never something a test or an agent trip.

    scutch record saleor --endpoint https://shop.example/graphql/ \\
        --operation saleor.customer.repair --input '{"customer_id":"cust_1", ...}'

The recorder wraps the transport, captures every request/response pair, and
writes them to ``fixtures/<provider>/<name>.json``. Token values are scrubbed
from the output: cassettes are committed, tokens are not.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .errors import ScutchError, UsageError

SECRET_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]{8,}", re.I)


class Recorder:
    """Wraps a live transport and records exchanges."""

    def __init__(self, transport: Any, name: str):
        self.transport = transport
        self.name = name
        self.exchanges: list[dict[str, Any]] = []
        self._orig_execute = transport.execute

    def __enter__(self) -> "Recorder":
        def execute(document: str, variables: dict[str, Any]) -> dict[str, Any]:
            response = self._orig_execute(document, variables)
            self.exchanges.append(
                {
                    "name": f"{self.name}.{len(self.exchanges) + 1}",
                    "request": {
                        "document": " ".join(document.split()),
                        "variables": variables,
                    },
                    "response": response,
                }
            )
            return response

        self.transport.execute = execute
        return self

    def __exit__(self, *exc: Any) -> None:
        self.transport.execute = self._orig_execute

    def to_json(self) -> dict[str, Any]:
        payload = {
            "provider": self.name,
            "note": "Recorded HTTP exchanges. Scrubbed of credentials; safe to commit.",
            "exchanges": self.exchanges,
        }
        text = json.dumps(payload, indent=2, sort_keys=True)
        payload["_scrubbed"] = len(SECRET_RE.findall(text))
        return json.loads(SECRET_RE.sub(r"\1<redacted>", text))


def record_operation(ws, provider: str, endpoint: str, operation: str, inputs: dict[str, Any],
                     approve: bool, out: Path | None = None, *,
                     document_only: bool = False, document: str | None = None,
                     variables: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run an operation live, capturing its HTTP exchanges."""
    import os

    from . import runner
    from .providers import ProviderConfig, resolve

    op = ws.registry.get(operation) if not document_only else None
    if op is not None and op.credentials:
        refs = list(op.credentials.values())
        if any(r.name != provider for r in refs):
            raise UsageError(
                f"{operation} uses provider(s) {[r.name for r in refs]}, not {provider!r}"
            )

    # Record against a live config with the endpoint overridden.
    live_cfg = ProviderConfig(
        name=provider,
        mode="live",
        env=ws.providers[provider].env if provider in ws.providers else None,
        notes=endpoint,
    )
    res = resolve(live_cfg, ws.root)
    api = res.load()

    class _Fixed:
        """A resolution whose load() always returns the recorded instance.

        runner._call() resolves the provider itself; handing it the same object
        is what keeps the recorder attached to the calls being made.
        """

        def __init__(self, api: Any):
            self._api = api

        def load(self) -> Any:
            return self._api

    fixed = _Fixed(api)

    if api._transport is None:
        raise UsageError("provider has no transport to record")
    if document_only:
        # Record a single query by variables, without running an operation.
        # Needed for negative cases (a missing entity) where the operation
        # raises before a cassette could otherwise be written.
        cassette_name = (out.stem if out else operation)
        with Recorder(api._transport, cassette_name) as rec:
            api._transport.execute(document, variables or {})
        payload = rec.to_json()
        target = out or (ws.root / "fixtures" / provider / f"{operation}.json")
        if not target.is_absolute():
            target = ws.root / target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2, sort_keys=True))
        return {
            "kind": "scutch.record/v1",
            "mode": "document",
            "provider": provider,
            "written": str(target.relative_to(ws.root)),
            "exchanges": len(payload["exchanges"]),
            "scrubbed_secrets": payload.get("_scrubbed", 0),
        }

    cassette_name = (out.stem if out else operation)
    with Recorder(api._transport, cassette_name) as rec:
        if op.plan_fn is not None:
            plan = runner._call(op.plan_fn, op, inputs, {"providers": {provider: fixed}, "ctx": None})
            if plan.is_empty():
                # Runtime never enters the apply function for an empty plan, so
                # recording it must not either — otherwise the cassette claims a
                # mutation that would never be sent.
                payload = rec.to_json() if False else None
                target = out or (ws.root / "fixtures" / provider / f"{operation}.json")
                if not target.is_absolute():
                    target = ws.root / target
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(rec.to_json(), indent=2, sort_keys=True))
                return {
                    "kind": "scutch.record/v1",
                    "mode": "plan-only",
                    "provider": provider,
                    "written": str(target.relative_to(ws.root)),
                    "exchanges": len(rec.exchanges),
                    "scrubbed_secrets": 0,
                    "note": "plan was empty; the apply function was not run, so no mutation is recorded",
                }
            elif not approve:
                raise UsageError(
                    "this operation intends to mutate; pass --approve to record it",
                    details={"changes": [c.to_json() for c in plan.changes]},
                )
        runner._call(op.fn, op, inputs, {"providers": {provider: fixed}, "ctx": None})

    payload = rec.to_json()
    target = out or (ws.root / "fixtures" / provider / f"{operation}.json")
    if not target.is_absolute():
        target = ws.root / target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return {
        "kind": "scutch.record/v1",
        "provider": provider,
        "operation": operation,
        "endpoint": endpoint,
        "written": str(target.relative_to(ws.root)) if target.is_relative_to(ws.root) else str(target),
        "exchanges": len(payload["exchanges"]),
        "scrubbed_secrets": payload.get("_scrubbed", 0),
    }
