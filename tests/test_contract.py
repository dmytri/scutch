"""Contract tests: schemas, validation, registry invariants."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Annotated, Literal

import jsonschema
import pytest


@dataclasses.dataclass
class WithHidden:
    visible: str = "v"
    hidden: Annotated[str, private()] = "x"


@dataclasses.dataclass
class Inner:
    secret_token: Annotated[str, private()] = "s3cr3t"
    ok: int = 1


@dataclasses.dataclass
class Outer:
    name: str = "n"
    inner: Inner = dataclasses.field(default_factory=Inner)
    listed: list = dataclasses.field(default_factory=lambda: [Inner()])


@dataclasses.dataclass
class Ok:
    ok: bool = True

from scutch.contract import Operation, Registry, operation
from scutch.errors import ContractError
from scutch.schema import Field, field, private, schema_for_type, to_public


def test_scalar_schemas():
    assert schema_for_type(int) == {"type": "integer"}
    assert schema_for_type(bool) == {"type": "boolean"}
    assert schema_for_type(str) == {"type": "string"}
    assert schema_for_type(list[str]) == {"type": "array", "items": {"type": "string"}}
    assert schema_for_type(Literal["a", "b"]) == {"enum": ["a", "b"], "type": "string"}
    assert schema_for_type(str | None) == {"anyOf": [{"type": "string"}, {"type": "null"}]}


def test_annotated_constraints_land_in_schema():
    tp = Annotated[str, field(pattern=r"^cust_", min_length=5, description="id")]
    s = schema_for_type(tp)
    assert s["pattern"] == "^cust_"
    assert s["minLength"] == 5
    assert s["description"] == "id"


def test_private_fields_are_not_in_result_schema():
    from scutch.schema import dataclass_schema

    s = dataclass_schema(WithHidden)
    assert "visible" in s["properties"]
    assert "hidden" not in s["properties"]


def test_projection_drops_private_and_reports_it():
    payload, redacted = to_public(Outer())
    text = json.dumps(payload)
    assert "s3cr3t" not in text
    assert payload["inner"] == {"ok": 1}
    assert payload["listed"] == [{"ok": 1}]
    assert set(redacted) == {"inner.secret_token", "listed[0].secret_token"}


def test_consequential_operation_without_idempotency_is_rejected():
    reg = Registry()

    with pytest.raises(ContractError, match="idempotency"):

        @operation(name="t.mutate", summary="x", effect="mutate", registry=reg)  # type: ignore[call-arg]
        def bad() -> Ok:
            return Ok()


def test_idempotency_key_must_name_real_parameters():
    reg = Registry()

    with pytest.raises(ContractError, match="not an input parameter"):

        @operation(
            name="t.mutate",
            summary="x",
            effect="mutate",
            idempotency_key=["nope"],
            registry=reg,  # type: ignore[call-arg]
        )
        def bad(a: str = "x") -> Ok:
            return Ok()


def test_name_pattern_enforced():
    reg = Registry()

    with pytest.raises(ContractError, match="dotted lowercase"):

        @operation(name="NotValid", summary="x", registry=reg)  # type: ignore[call-arg]
        def bad() -> Ok:
            return Ok()


def test_workspace_operations_all_validate():
    from scutch.workspace import load_workspace

    ws = load_workspace(Path(__file__).resolve().parents[1])
    for name, problems in ws.registry.validate_all().items():
        assert problems == [], f"{name}: {problems}"


def test_input_schema_round_trips_through_jsonschema():
    from scutch.workspace import load_workspace

    ws = load_workspace(Path(__file__).resolve().parents[1])
    op = ws.registry.get("orders.fulfil")
    schema = op.input_schema()
    jsonschema.validate({"order_id": "ord_a1"}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"order_id": "nope"}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"order_id": "ord_a1", "extra": 1}, schema)


def test_result_schema_matches_projected_output():
    from scutch.schema import to_public as project
    from scutch.workspace import load_workspace

    ws = load_workspace(Path(__file__).resolve().parents[1])
    op = ws.registry.get("orders.list")
    res = op.result_type(orders=[], count=0)
    payload, _ = project(res)
    jsonschema.validate(payload, op.output_schema())
