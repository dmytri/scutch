"""The declared shape of a provider's response.

A replay cassette is a memory of bytes. This is the schema that memory is
checked against, and it is *the same* check applied to a live response — so a
field that Saleor renames fails here, by name, instead of being read through
``.get(field, default)`` and quietly becoming "no data".

That distinction matters because "the API renamed this" and "this customer has
no permissions" are otherwise indistinguishable, and the second is a legitimate
state that must plan a mutation while the first must refuse to.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from .errors import ProviderError


class FieldError(ProviderError):
    """A response did not match the declared shape."""


@dataclasses.dataclass(frozen=True)
class Permission:
    """One `channelPermissions` entry: a channel slug and its permissions."""

    channel: str
    permissions: tuple[str, ...]

    @classmethod
    def parse(cls, raw: Any, where: str) -> "Permission":
        if not isinstance(raw, dict):
            raise FieldError(f"{where}: expected an object, got {type(raw).__name__}")
        channel = raw.get("channel")
        if not isinstance(channel, dict) or "slug" not in channel:
            raise FieldError(
                f"{where}: missing `channel.slug`",
                hint="check `channelPermissions { channel { slug } permissions }`",
                details={"keys": sorted(raw)},
            )
        permissions = raw.get("permissions")
        if not isinstance(permissions, list):
            raise FieldError(
                f"{where}: `permissions` missing or not a list",
                details={"keys": sorted(raw)},
            )
        return cls(channel=str(channel["slug"]), permissions=tuple(str(p) for p in permissions))

    def to_json(self) -> dict[str, Any]:
        return {"channel": {"slug": self.channel}, "permissions": list(self.permissions)}


@dataclasses.dataclass(frozen=True)
class Customer:
    """A customer as the API returns it, validated on the way in."""

    customer_id: str
    email: str | None
    permissions: tuple[Permission, ...]

    @classmethod
    def parse(cls, raw: Any, where: str = "customer") -> "Customer":
        if not isinstance(raw, dict):
            raise FieldError(f"{where}: expected an object, got {type(raw).__name__}")
        if "id" not in raw:
            raise FieldError(
                f"{where}: missing `id`",
                hint="the API's customer shape changed",
                details={"keys": sorted(raw)},
            )
        # Required, not defaulted. `.get("channelPermissions", [])` is exactly
        # the read that made a renamed field look like an empty permission list.
        if "channelPermissions" not in raw:
            raise FieldError(
                f"{where}: missing `channelPermissions` in the response",
                hint="if the API renamed this field, update the query and this parser together",
                details={"keys": sorted(raw)},
            )
        entries = raw["channelPermissions"]
        if not isinstance(entries, list):
            raise FieldError(f"{where}: `channelPermissions` is not a list")
        return cls(
            customer_id=str(raw["id"]),
            email=raw.get("email"),
            permissions=tuple(
                Permission.parse(entry, f"{where}.channelPermissions[{i}]")
                for i, entry in enumerate(entries)
            ),
        )

    def permissions_on(self, channel: str) -> list[str]:
        for entry in self.permissions:
            if entry.channel == channel:
                return list(entry.permissions)
        return []

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.customer_id,
            "email": self.email,
            "channelPermissions": [p.to_json() for p in self.permissions],
        }
