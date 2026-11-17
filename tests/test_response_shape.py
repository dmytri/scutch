"""The response shape is a type, not an assumption.

A cassette is a memory of bytes. These tests are about the *schema* that memory
is checked against: a renamed field must fail by name, not be read as "no data".

The failing case they lock is the one that motivated them: with `.get(field, [])`
a renamed `channelPermissions` became an empty list, and the operation then
planned a mutation for a customer who already had the permission.
"""

from __future__ import annotations

import dataclasses

import pytest

from scutch.responses import Customer, FieldError, Permission

CUSTOMER = {
    "id": "cust_123",
    "email": "grace@example.com",
    "channelPermissions": [
        {"channel": {"slug": "retail"}, "permissions": ["read_orders"]},
    ],
}


def test_a_real_response_parses():
    c = Customer.parse(CUSTOMER)
    assert c.customer_id == "cust_123"
    assert c.permissions_on("retail") == ["read_orders"]
    assert c.permissions_on("wholesale") == []
    assert c.to_json() == CUSTOMER


def test_a_renamed_field_fails_by_name():
    """The regression: this used to look like a customer with no permissions."""
    renamed = {
        "id": "cust_123",
        "email": "grace@example.com",
        "permissions": CUSTOMER["channelPermissions"],
    }
    with pytest.raises(FieldError) as exc:
        Customer.parse(renamed)
    message = str(exc.value)
    assert "channelPermissions" in message
    assert "cust_123" in message or "customer" in message


def test_the_error_lists_the_keys_it_did_see():
    renamed = {"id": "c", "permissions": []}
    with pytest.raises(FieldError) as exc:
        Customer.parse(renamed)
    details = getattr(exc.value, "details", {}) or {}
    assert "permissions" in details.get("keys", [])


def test_a_missing_id_is_an_error():
    with pytest.raises(FieldError):
        Customer.parse({"channelPermissions": []})


def test_a_channel_entry_without_a_slug_is_an_error():
    broken = {"id": "c", "channelPermissions": [{"permissions": ["read_orders"]}]}
    with pytest.raises(FieldError) as exc:
        Customer.parse(broken)
    assert "channel.slug" in str(exc.value)


def test_permissions_that_are_not_a_list_are_an_error():
    broken = {"id": "c", "channelPermissions": [{"channel": {"slug": "retail"}, "permissions": "read_orders"}]}
    with pytest.raises(FieldError):
        Customer.parse(broken)


def test_an_empty_permission_list_is_valid_not_an_error():
    """The legitimate state: no permissions is data, not a broken response."""
    c = Customer.parse({"id": "c", "channelPermissions": []})
    assert c.permissions_on("retail") == []
    assert c.permissions == ()


def test_field_error_is_a_provider_error():
    from scutch.errors import ProviderError

    assert issubclass(FieldError, ProviderError)


def test_the_type_is_immutable():
    c = Customer.parse(CUSTOMER)
    assert dataclasses.is_dataclass(c)
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.customer_id = "other"  # type: ignore[misc]
