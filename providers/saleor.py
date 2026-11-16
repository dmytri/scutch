"""Saleor provider: the seam between operations and the Saleor GraphQL API.

Fixture mode reads ``fixtures/saleor/``. Live mode talks HTTP using a token
resolved by the runtime (``SCUTCH_SALEOR_TOKEN``). Neither mode ever lets a
credential value reach an envelope: the token lives only on this object, and
``redact()`` proves it is absent from anything we return.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from scutch.providers import Resolution, fixture_files, state_path


class SaleorApi:
    def __init__(self, resolution: Resolution):
        self.mode = resolution.mode
        self.endpoint = resolution.config.notes or "https://saleor.example/graphql/"
        self._secret = resolution.secret
        self._state_file = state_path(resolution) if resolution.mode == "fixture" else None
        if resolution.mode == "fixture":
            self._fixtures: dict[str, Any] = fixture_files(resolution)
            self._live: dict[str, Any] | None = None
        else:
            self._live = {}
        self._resolution = resolution

    # -- fixture state ---------------------------------------------------

    def _state(self) -> dict[str, Any]:
        """Applied fixture state, overlaid on the read-only seed."""
        if self._live is not None:
            return self._live
        assert self._state_file is not None
        if self._state_file.is_file():
            self._live = json.loads(self._state_file.read_text())
        else:
            self._live = {"customers": copy.deepcopy(self._fixtures.get("customers", {})),
                          "orders": copy.deepcopy(self._fixtures.get("orders", {}))}
        return self._live

    def _persist(self) -> None:
        if self._live is None or self._state_file is None:
            return
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(json.dumps(self._live, indent=2))

    # -- reads -----------------------------------------------------------

    def _fixture_customer(self, customer_id: str) -> dict[str, Any]:
        customers = self._state()["customers"]
        if customer_id not in customers:
            from scutch.errors import NotFound

            raise NotFound(
                f"customer {customer_id!r} does not exist",
                hint="use scutch.list_customers to find a valid id",
                details={"available": sorted(customers)},
            )
        # Copy on read: callers must never alias mutable provider state.
        return copy.deepcopy(customers[customer_id])

    def get_customer(self, customer_id: str) -> dict[str, Any]:
        if self.mode == "fixture":
            return self._fixture_customer(customer_id)
        return self._graphql(
            """
            query Customer($id: ID!) {
              user(id: $id) { id email channelPermissions { channel { slug } permissions } }
            }
            """,
            {"id": customer_id},
        )["user"]

    def list_customers(self, limit: int = 20) -> list[dict[str, Any]]:
        if self.mode == "fixture":
            customers = self._state()["customers"]
            return [
                {
                    "customer_id": cid,
                    "email": c.get("email"),
                    "channel_permissions": c.get("channelPermissions", []),
                }
                for cid, c in list(customers.items())[:limit]
            ]
        data = self._graphql(
            "query Customers($n: Int!) { users(first: $n) { edges { node { id email channelPermissions { channel { slug } permissions } } } } }",
            {"n": limit},
        )
        return [
            {
                "customer_id": e["node"]["id"],
                "email": e["node"]["email"],
                "channel_permissions": e["node"]["channelPermissions"],
            }
            for e in data["users"]["edges"]
        ]

    def orders(self, customer_id: str) -> list[dict[str, Any]]:
        if self.mode == "fixture":
            return self._state()["orders"].get(customer_id, [])
        return self._graphql(
            "query Orders($id: ID!) { user(id: $id) { orders(first: 10) { edges { node { id status total { gross { amount } } } } } } }",
            {"id": customer_id},
        )["user"]["orders"]["edges"]

    # -- mutation --------------------------------------------------------

    def apply_permission(self, customer_id: str, channel: str, permission: str) -> dict[str, Any]:
        """Add one channel permission. Called only from an approved apply path."""
        if self.mode == "fixture":
            state = self._state()["customers"]
            if customer_id not in state:
                self._fixture_customer(customer_id)  # raises NotFound with details
            customer = state[customer_id]
            perms = customer.setdefault("channelPermissions", [])
            for entry in perms:
                if entry.get("channel", {}).get("slug") == channel:
                    if permission not in entry.setdefault("permissions", []):
                        entry["permissions"].append(permission)
                    self._persist()
                    return {"applied": True, "already_present": False}
            perms.append({"channel": {"slug": channel}, "permissions": [permission]})
            self._persist()
            return {"applied": True, "already_present": False}
        return self._graphql(
            """
            mutation Grant($id: ID!, $channel: String!, $permission: PermissionEnum!) {
              channelUpdatePermission(userId: $id, channelSlug: $channel, permission: $permission) {
                user { id }
                errors { field message code }
              }
            }
            """,
            {"id": customer_id, "channel": channel, "permission": permission},
        )

    # -- internals -------------------------------------------------------

    def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        import urllib.request

        assert self._secret, "live provider resolved without a secret"
        body = json.dumps({"query": query, "variables": variables}).encode()
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
            from scutch.errors import ProviderError

            raise ProviderError("Saleor rejected the request", details={"errors": payload["errors"]})
        return payload["data"]

    def __repr__(self) -> str:  # never leak the token through repr
        return f"SaleorApi(mode={self.mode!r}, endpoint={self.endpoint!r}, secret=<redacted>)"


def make(resolution: Resolution) -> SaleorApi:
    return SaleorApi(resolution)
