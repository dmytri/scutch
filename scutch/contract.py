"""The operation contract and its registry.

An operation is an ordinary typed Python function decorated with
``@operation(...)``. The decorator reads the signature and metadata and
produces an ``Operation`` descriptor. That descriptor is the single source of
truth for ``describe``, JSON Schemas, TUI forms, examples and agent-facing
instructions. If the implementation drifts from the declared contract,
``validate()`` fails — self-description stays executable truth.
"""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
import re
import typing
from typing import Any, Callable, Iterable, Literal, get_args, get_origin

from .errors import ContractError, NotFound
from .schema import (
    Field,
    dataclass_schema,
    field_meta,
    is_private,
    schema_for_type,
    strip_annotated,
    to_public,
)

# --------------------------------------------------------------------------
# Enumerations of behaviour


Effect = Literal["read", "plan_apply", "mutate"]
CredentialSpec = dict[str, "ProviderRef"]


@dataclasses.dataclass(frozen=True)
class ProviderRef:
    """A named provider a caller must resolve. Never a credential value."""

    name: str

    def handle(self) -> dict[str, str]:
        return {"provider": self.name, "resolution": "runtime"}


Status = Literal[
    "needs_input",
    "needs_approval",
    "running",
    "partial",
    "complete",
    "failed",
    "cancelled",
]

ALL_STATUSES: tuple[str, ...] = (
    "needs_input",
    "needs_approval",
    "running",
    "partial",
    "complete",
    "failed",
    "cancelled",
)


@dataclasses.dataclass
class Operation:
    name: str
    version: str
    summary: str
    fn: Callable[..., Any]
    effect: Effect
    credentials: CredentialSpec
    idempotency_key: tuple[str, ...]
    approval: Literal["none", "auto", "required"]
    result_type: Any
    examples: list[dict[str, Any]]
    statuses: tuple[str, ...]
    deprecations: dict[str, str]
    implementation: str
    module: str
    read_only: bool
    external_reads: tuple[str, ...]
    intended_effects: tuple[str, ...]
    artifacts: tuple[str, ...]
    plan_fn: Callable[..., Any] | None = None
    description: str = ""
    deprecated: str | None = None
    replaced_by: str | None = None

    # -- derived contract -------------------------------------------------

    @property
    def params(self) -> list[inspect.Parameter]:
        sig = inspect.signature(self.fn)
        return [
            p
            for p in sig.parameters.values()
            if p.name not in {"api"} and p.kind is not inspect.Parameter.VAR_KEYWORD
        ]

    @property
    def provider_params(self) -> list[inspect.Parameter]:
        return [
            p for p in inspect.signature(self.fn).parameters.values() if p.name == "api"
        ]
    @property
    def requires_approval(self) -> bool:
        return self.approval in ("auto", "required") and self.effect != "read"


    def input_schema(self) -> dict[str, Any]:
        hints = typing.get_type_hints(self.fn, include_extras=True)
        properties: dict[str, Any] = {}
        required: list[str] = []
        for p in self.params:
            tp = hints.get(p.name, Any)
            schema = schema_for_type(tp)
            if p.default is not inspect.Parameter.empty:
                schema["default"] = _jsonable(p.default)
            properties[p.name] = schema
            if (
                p.default is inspect.Parameter.empty
                and p.kind is not inspect.Parameter.VAR_POSITIONAL
            ):
                required.append(p.name)
        schema: dict[str, Any] = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": f"{self.name} input",
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
        }
        if required:
            schema["required"] = required
        return schema

    def output_schema(self) -> dict[str, Any]:
        schema = dataclass_schema(self.result_type)
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": f"{self.name} result",
            **schema,
        }

    def canonical_inputs(self) -> dict[str, Any]:
        """Defaults filled in, so every caller can reproduce the invocation."""
        out: dict[str, Any] = {}
        for p in self.params:
            if p.default is not inspect.Parameter.empty:
                out[p.name] = _jsonable(p.default)
        return out

    # -- self-description -------------------------------------------------

    def describe(self) -> dict[str, Any]:
        hints = typing.get_type_hints(self.fn, include_extras=True)
        params = []
        for p in self.params:
            tp = hints.get(p.name, Any)
            meta = field_meta(tp)
            entry: dict[str, Any] = {
                "name": p.name,
                "schema": schema_for_type(tp),
                "required": p.default is inspect.Parameter.empty,
            }
            if p.default is not inspect.Parameter.empty:
                entry["default"] = _jsonable(p.default)
            if meta and meta.sensitive:
                entry["sensitive"] = True
            if meta and meta.description:
                entry["description"] = meta.description
            params.append(entry)

        return {
            "kind": "scutch.operation/v1",
            "name": self.name,
            "version": self.version,
            "summary": self.summary,
            "description": inspect.getdoc(self.fn) or "",
            "effect": self.effect,
            "approval": self.approval,
            "credentials": {k: v.handle() for k, v in self.credentials.items()},
            "idempotency_key": list(self.idempotency_key),
            "parameters": params,
            "input_schema": self.input_schema(),
            "result_schema": self.output_schema(),
            "statuses": list(self.statuses) or list(ALL_STATUSES),
            "examples": self.examples,
            "source": {
                "module": self.module,
                "implementation": self.implementation,
                "spec": f"specs/{self.name}.feature",
            },
            "external_reads": list(self.external_reads),
            "intended_effects": list(self.intended_effects),
            "artifacts": list(self.artifacts),
            "deprecated": self.deprecated,
            "replaced_by": self.replaced_by,
            "invocation": {
                "argv": ["scutch", "run", self.name, "--input", "@-", "--format", "json"],
                "notes": "Pass canonical input JSON on stdin. Never invent a "
                "session token: the envelope's next.argv is the only continuation.",
            },
        }

    def validate(self) -> list[str]:
        """Prove the implementation matches the declared contract."""
        problems: list[str] = []
        try:
            schema = self.input_schema()
            dataclass_schema(self.result_type)
        except ContractError as exc:  # pragma: no cover - defensive
            return [str(exc)]

        if not re.fullmatch(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+", self.name):
            problems.append(f"name {self.name!r} is not a dotted lowercase identifier")
        if self.effect not in ("read", "plan_apply", "mutate"):
            problems.append(f"effect {self.effect!r} is invalid")
        if self.approval not in ("none", "auto", "required"):
            problems.append(f"approval {self.approval!r} is invalid")
        if self.effect in ("plan_apply", "mutate") and self.approval == "none":
            problems.append("consequential operations cannot declare approval='none'")
        for key in self.idempotency_key:
            if key not in schema["properties"]:
                problems.append(f"idempotency key {key!r} is not an input parameter")
        if self.effect != "read" and not self.idempotency_key:
            problems.append("consequential operations must declare an idempotency key")
        if is_private(self.result_type):
            problems.append("result type cannot be private")
        for ex in self.examples:
            unknown = set(ex.get("input", {})) - set(schema["properties"])
            if unknown:
                problems.append(f"example uses unknown inputs: {sorted(unknown)}")
        return problems

    # -- invocation -------------------------------------------------------

    def prepare(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Camel/aliased input as supplied by the caller, canonicalised."""
        return dict(raw)


@dataclasses.dataclass
class Invocation:
    operation: Operation
    inputs: dict[str, Any]

    def idempotency_value(self) -> str | None:
        if not self.operation.idempotency_key:
            return None
        parts = [str(self.inputs.get(k)) for k in self.operation.idempotency_key]
        return "|".join(parts)

    def fingerprint(self) -> str:
        canon = json.dumps(
            {
                "operation": self.operation.name,
                "inputs": self.inputs,
                "idempotency": self.idempotency_value(),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canon.encode()).hexdigest()


# --------------------------------------------------------------------------
# Registry


class Registry:
    def __init__(self) -> None:
        self._ops: dict[str, Operation] = {}
        self._module: dict[str, str] = {}

    def register(self, op: Operation) -> Operation:
        if op.name in self._ops:
            raise ContractError(f"operation {op.name} registered twice")
        problems = op.validate()
        if problems:
            raise ContractError(
                f"operation {op.name} violates its contract: " + "; ".join(problems)
            )
        self._ops[op.name] = op
        return op

    def add(self, op: Operation) -> Operation:
        return self.register(op)

    def get(self, name: str) -> Operation:
        try:
            return self._ops[name]
        except KeyError:
            matches = [n for n in self._ops if n.startswith(name)]
            if len(matches) == 1:
                return self._ops[matches[0]]
            if len(matches) > 1:
                raise NotFound(
                    f"ambiguous operation {name!r}",
                    hint="candidates: " + ", ".join(sorted(matches)),
                )
            raise NotFound(f"unknown operation {name!r}", hint="run `scutch list`")

    def __contains__(self, name: object) -> bool:
        return name in self._ops

    def names(self) -> list[str]:
        return sorted(self._ops)

    def all(self) -> list[Operation]:
        return [self._ops[n] for n in self.names()]

    def validate_all(self) -> dict[str, list[str]]:
        return {name: op.validate() for name, op in self._ops.items()}


REGISTRY = Registry()


# --------------------------------------------------------------------------
# Decorator


def operation(
    *,
    name: str,
    summary: str,
    effect: Effect = "read",
    credentials: CredentialSpec | None = None,
    idempotency_key: Iterable[str] = (),
    approval: Literal["none", "auto", "required"] | None = None,
    examples: Iterable[dict[str, Any]] = (),
    version: str = "1",
    read_only: bool | None = None,
    external_reads: Iterable[str] = (),
    intended_effects: Iterable[str] = (),
    artifacts: Iterable[str] = (),
    deprecated: str | None = None,
    replaced_by: str | None = None,
    statuses: Iterable[str] = (),
    registry: "Registry | None" = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Declare an operation. Metadata here plus the signature is the contract."""

    effective_approval = approval
    if effect in ("plan_apply", "mutate") and effective_approval is None:
        effective_approval = "required"
    if effective_approval is None:
        effective_approval = "none"

    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        hints = typing.get_type_hints(fn, include_extras=True)
        ret = hints.get("return")
        if ret is None:
            raise ContractError(f"{name}: operation must declare a result type")
        result_type = strip_annotated(ret)
        if not dataclasses.is_dataclass(result_type):
            raise ContractError(
                f"{name}: result type must be a dataclass, got {result_type!r}"
            )
        plan_fn = getattr(fn, "__scutch_plan__", None)
        op = Operation(
            name=name,
            plan_fn=plan_fn,
            version=version,
            summary=summary,
            fn=fn,
            effect=effect,
            credentials=dict(credentials or {}),
            idempotency_key=tuple(idempotency_key),
            approval=effective_approval,
            result_type=result_type,
            examples=[dict(e) for e in examples],
            statuses=tuple(statuses),
            deprecations={},
            implementation=f"{fn.__module__}.{fn.__qualname__}",
            module=fn.__module__,
            read_only=read_only if read_only is not None else effect == "read",
            external_reads=tuple(external_reads),
            intended_effects=tuple(intended_effects),
            artifacts=tuple(artifacts),
            description=inspect.getdoc(fn) or "",
            deprecated=deprecated,
            replaced_by=replaced_by,
        )
        setattr(fn, "__scutch_operation__", op)
        (registry or REGISTRY).register(op)
        return fn

    return decorate


def plan_for(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Attach the planning half of a plan/apply operation.

    Separate from ``@operation`` so the read path (what would change?) and the
    write path (do it) are ordinary, independently testable functions.
    """

    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        op = REGISTRY.get(name)
        op.plan_fn = fn
        return fn

    return decorate


def load_operations(*modules: str) -> Registry:
    """Import operation modules so their decorators register them."""
    import importlib

    for mod in modules:
        importlib.import_module(mod)
    return REGISTRY


# --------------------------------------------------------------------------
# helpers


def _is_enum(tp: Any) -> bool:
    import enum

    return isinstance(tp, type) and issubclass(tp, enum.Enum)


def _jsonable(value: Any) -> Any:
    import enum

    if isinstance(value, enum.Enum):
        return value.value
    if dataclasses.is_dataclass(value):
        return {f.name: _jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    return value
