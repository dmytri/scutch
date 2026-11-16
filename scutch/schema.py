"""Type -> JSON Schema projection, plus public/private result projection.

The contract is executable truth: input schemas, result schemas and the
projection of "safe" result fields are all derived from the Python
signatures and dataclasses that implement an operation. Nothing here is
hand-maintained documentation.
"""

from __future__ import annotations

import dataclasses
import enum
import types
import typing
from typing import Any, Literal, Union, get_args, get_origin

from .errors import ContractError


# --------------------------------------------------------------------------
# Annotated markers


@dataclasses.dataclass(frozen=True)
class Field:
    """Constraints and documentation attached to a parameter via Annotated."""

    description: str | None = None
    pattern: str | None = None
    min: float | None = None
    max: float | None = None
    min_length: int | None = None
    max_length: int | None = None
    choices: tuple[str, ...] | None = None
    format: str | None = None
    sensitive: bool = False
    example: Any = None


@dataclasses.dataclass(frozen=True)
class Private:
    """Marks a result field that must never enter a public envelope."""


def field(**kwargs: Any) -> Field:
    return Field(**kwargs)


def private() -> Private:
    return Private()


# --------------------------------------------------------------------------
# JSON Schema


def _apply_field(schema: dict[str, Any], meta: Field) -> dict[str, Any]:
    out = dict(schema)
    if meta.description:
        out["description"] = meta.description
    if meta.pattern:
        out["pattern"] = meta.pattern
    if meta.min is not None:
        out["minimum"] = meta.min
    if meta.max is not None:
        out["maximum"] = meta.max
    if meta.min_length is not None:
        out["minLength"] = meta.min_length
    if meta.max_length is not None:
        out["maxLength"] = meta.max_length
    if meta.choices:
        out["enum"] = list(meta.choices)
    if meta.format:
        out["format"] = meta.format
    if meta.example is not None:
        out["examples"] = [meta.example]
    return out


def is_private(tp: Any) -> bool:
    return get_origin(tp) is typing.Annotated and any(
        isinstance(a, Private) for a in get_args(tp)[1:]
    )


def field_meta(tp: Any) -> Field | None:
    if get_origin(tp) is not typing.Annotated:
        return None
    for a in get_args(tp)[1:]:
        if isinstance(a, Field):
            return a
    return None


def strip_annotated(tp: Any) -> Any:
    """Drop Annotated metadata, keeping only the base type."""
    while get_origin(tp) is typing.Annotated:
        tp = get_args(tp)[0]
    return tp


def schema_for_type(tp: Any) -> dict[str, Any]:
    """Project a Python type annotation onto JSON Schema (draft 2020-12)."""
    origin = get_origin(tp)

    if origin is typing.Annotated:
        base = get_args(tp)[0]
        schema = schema_for_type(base)
        meta = field_meta(tp)
        return _apply_field(schema, meta) if meta else schema

    if tp is str:
        return {"type": "string"}
    if tp is bool:
        return {"type": "boolean"}
    if tp is int:
        return {"type": "integer"}
    if tp is float:
        return {"type": "number"}
    if tp is None or tp is type(None):
        return {"type": "null"}
    if tp is Any:
        return {}

    if origin in (typing.Union, types.UnionType):
        args = [a for a in get_args(tp)]
        non_null = [a for a in args if a is not type(None)]
        if len(non_null) == 1 and len(args) == 2:
            inner = schema_for_type(non_null[0])
            return {"anyOf": [inner, {"type": "null"}]}
        return {"anyOf": [schema_for_type(a) for a in args]}

    if origin is Literal:
        values = list(get_args(tp))
        kinds = {type(v) for v in values}
        schema: dict[str, Any] = {"enum": values}
        if kinds == {str}:
            schema["type"] = "string"
        elif kinds == {int}:
            schema["type"] = "integer"
        elif kinds == {bool}:
            schema["type"] = "boolean"
        return schema

    if origin in (list, set, tuple, typing.Sequence):
        args = get_args(tp)
        item = args[0] if args and args[0] is not Ellipsis else Any
        return {"type": "array", "items": schema_for_type(item)}
    if origin in (dict, typing.Mapping):
        args = get_args(tp)
        value = args[1] if len(args) == 2 else Any
        return {"type": "object", "additionalProperties": schema_for_type(value)}

    if isinstance(tp, type) and issubclass(tp, enum.Enum):
        values = [m.value for m in tp]
        schema = {"enum": values}
        kinds = {type(v) for v in values}
        if kinds == {str}:
            schema["type"] = "string"
        elif kinds == {int}:
            schema["type"] = "integer"
        return schema

    if isinstance(tp, type) and dataclasses.is_dataclass(tp):
        return dataclass_schema(tp)

    raise ContractError(f"cannot project type {tp!r} onto JSON Schema")


def dataclass_schema(cls: type) -> dict[str, Any]:
    hints = typing.get_type_hints(cls, include_extras=True)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for f in dataclasses.fields(cls):
        if is_private(hints.get(f.name, Any)):
            continue
        tp = hints.get(f.name, Any)
        properties[f.name] = schema_for_type(tp)
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
            required.append(f.name)
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    if cls.__doc__:
        schema["title"] = cls.__doc__.strip().splitlines()[0]
    return schema


# --------------------------------------------------------------------------
# Public projection


def to_public(obj: Any) -> tuple[Any, list[str]]:
    """Project an operation result into public JSON.

    Returns (payload, redacted_field_paths). Fields annotated ``Private`` are
    dropped and reported by name so callers can see that something was held
    back without seeing its value.
    """
    redacted: list[str] = []
    payload = _to_public(obj, "", redacted)
    return payload, redacted


def _to_public(obj: Any, path: str, redacted: list[str]) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        hints = typing.get_type_hints(type(obj), include_extras=True)
        out: dict[str, Any] = {}
        for f in dataclasses.fields(obj):
            child = f"{path}.{f.name}" if path else f.name
            if is_private(hints.get(f.name, Any)):
                redacted.append(child)
                continue
            out[f.name] = _to_public(getattr(obj, f.name), child, redacted)
        return out
    if isinstance(obj, dict):
        return {str(k): _to_public(v, f"{path}.{k}" if path else str(k), redacted) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_to_public(v, f"{path}[{i}]", redacted) for i, v in enumerate(obj)]
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    raise ContractError(
        f"result value at {path or '<root>'} is not JSON-projectable: {type(obj).__name__}"
    )
