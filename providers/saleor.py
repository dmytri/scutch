"""Saleor provider: the seam between operations and the Saleor GraphQL API.

There is **one** code path. Every mode goes through ``GraphQLTransport`` and the
real query/mutation text; modes differ only in what answers the HTTP call:

- ``live``  — real ``urllib``; token from ``SCUTCH_SALEOR_TOKEN``.
- ``replay`` — recorded exchanges from ``fixtures/<provider>/*.json``. Unknown
  requests fail loudly (no silent fallback), so a changed query is a test
  failure, not a green run.

The transport is where API assumptions live — query text, variable names, error
mapping, auth header. Doubling *below* it (at the socket) keeps all of that real.
Doubling *above* it (a fake adapter) would delete exactly what contract tests
exist to check.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Any

from scutch.errors import NotFound, ProviderError
from scutch.responses import Customer, FieldError, Permission
from scutch.providers import Resolution, replay_data


# --------------------------------------------------------------------------
# GraphQL documents. These strings are the API contract.


Q_CUSTOMER = """
query Customer($id: ID!) {
  user(id: $id) {
    id
    email
    channelPermissions { channel { slug } permissions }
  }
}
"""

Q_CUSTOMERS = """
query Customers($n: Int!) {
  users(first: $n) {
    edges { node { id email channelPermissions { channel { slug } permissions } } }
  }
}
"""

Q_ORDERS = """
query Orders($id: ID!) {
  user(id: $id) {
    orders(first: 10) { edges { node { id status total { gross { amount } } } } }
  }
}
"""

M_GRANT = """
mutation Grant($id: ID!, $channel: String!, $permission: PermissionEnum!) {
  channelUpdatePermission(userId: $id, channelSlug: $channel, permission: $permission) {
    user { id }
    errors { field message code }
  }
}
"""


# --------------------------------------------------------------------------
# Transport


class GraphQLTransport:
    """Sends one GraphQL document. Live and replay differ only here."""

    def __init__(self, endpoint: str, secret: str | None, replay: dict[str, Any] | None,
                 root: Path | None = None):
        self.endpoint = endpoint
        self._root = root or Path.cwd()
        self._secret = secret
        self._replay = replay
        self.sent: list[dict[str, Any]] = []
        # Identical requests can legitimately return different responses (a read
        # before and after a mutation). Consumption is therefore positional:
        # each match advances a per-request cursor.
        self._cursor: dict[str, int] = {}

    @property
    def mode(self) -> str:
        return "replay" if self._replay is not None else "live"

    def execute(self, document: str, variables: dict[str, Any]) -> dict[str, Any]:
        entry = {"kind": "mutation" if "mutation" in document else "query",
                 "document": " ".join(document.split()), "variables": variables}
        self.sent.append(entry)
        self._log(entry)
        # Workspace-level sink: a caller does not have to hold this instance to
        # see what was sent. `scutch workflow` reads it for its wire assertions.
        from scutch.providers import record_wire

        record_wire(self._root, entry)
        if self._replay is not None:
            return self._from_replay(document, variables)
        return self._over_http(document, variables)

    @staticmethod
    def _log(entry: dict[str, Any]) -> None:
        """Append to the optional request log.

        Tests set SCUTCH_WIRE_LOG so a scenario can assert on what was sent,
        without trusting the provider's own bookkeeping.
        """
        path = os.environ.get("SCUTCH_WIRE_LOG")
        if not path:
            return
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")

    # -- replay ----------------------------------------------------------

    def _from_replay(self, document: str, variables: dict[str, Any]) -> dict[str, Any]:
        key = {"document": " ".join(document.split()), "variables": variables}
        fingerprint = json.dumps(key, sort_keys=True)
        matches = [e for e in self._replay["exchanges"] if e["request"] == key]
        if not matches:
            raise ProviderError(
                "no recorded response for this request",
                hint="the query or its variables changed; re-record the fixture",
                details={
                    "recorded": [e["name"] for e in self._replay["exchanges"]],
                    "request": key,
                },
            )
        index = self._cursor.get(fingerprint, 0)
        if index >= len(matches):
            raise ProviderError(
                "replay exhausted for this request",
                hint=f"{len(matches)} response(s) recorded but more were requested; "
                     "the operation made an extra call",
                details={"request": key, "recorded": len(matches)},
            )
        self._cursor[fingerprint] = index + 1
        return matches[index]["response"]

    # -- http ------------------------------------------------------------

    def _over_http(self, document: str, variables: dict[str, Any]) -> dict[str, Any]:
        assert self._secret, "live transport resolved without a secret"
        body = json.dumps({"query": document, "variables": variables}).encode()
        req = urllib.request.Request(
            self.endpoint,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._secret}",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
            payload = json.loads(resp.read())
        if payload.get("errors"):
            raise ProviderError("Saleor rejected the request", details={"errors": payload["errors"]})
        return payload["data"]


# --------------------------------------------------------------------------
# API


class SaleorApi:
    def __init__(self, resolution: Resolution):
        endpoint = resolution.config.notes or "https://saleor.example/graphql/"
        self._transport = GraphQLTransport(
            endpoint,
            resolution.secret,
            replay_data(resolution) if resolution.mode == "replay" else None,
            resolution.root,
        )
        self.endpoint = endpoint

    @property
    def mode(self) -> str:
        return self._transport.mode

    def __repr__(self) -> str:  # never leak the token
        return f"SaleorApi(mode={self.mode!r}, endpoint={self.endpoint!r}, secret=<redacted>)"

    # -- reads -----------------------------------------------------------

    def get_customer(self, customer_id: str) -> Customer:
        data = self._transport.execute(Q_CUSTOMER, {"id": customer_id})
        user = data.get("user")
        if not user:
            raise NotFound(
                f"customer {customer_id!r} does not exist",
                hint="use saleor.customer.list to find a valid id",
            )
        return Customer.parse(user, where=f"customer[{customer_id}]")

    def list_customers(self, limit: int = 20) -> list[Customer]:
        data = self._transport.execute(Q_CUSTOMERS, {"n": limit})
        edges = (data.get("users") or {}).get("edges")
        if not isinstance(edges, list):
            raise FieldError(
                "list_customers: response has no `users.edges` list",
                details={"keys": sorted(data)},
            )
        return [
            Customer.parse(edge.get("node"), where=f"users.edges[{i}].node")
            for i, edge in enumerate(edges)
        ]

    def orders(self, customer_id: str) -> list[dict[str, Any]]:
        data = self._transport.execute(Q_ORDERS, {"id": customer_id})
        return data["user"]["orders"]["edges"]

    # -- mutation --------------------------------------------------------

    def apply_permission(self, customer_id: str, channel: str, permission: str) -> dict[str, Any]:
        data = self._transport.execute(
            M_GRANT, {"id": customer_id, "channel": channel, "permission": permission}
        )
        result = data.get("channelUpdatePermission") or {}
        errors = result.get("errors") or []
        if errors:
            raise ProviderError(
                "Saleor refused the permission change",
                details={"errors": errors},
            )
        return {"applied": True, "user": (result.get("user") or {}).get("id")}


def make(resolution: Resolution) -> SaleorApi:
    return SaleorApi(resolution)
