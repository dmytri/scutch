# Authoring a workspace

A workspace *is* a repository. Scutch reads `scutch.toml`, imports
`operations/**/*.py`, and resolves `providers/*.py` on demand. Nothing global is
consulted except the environment variables a provider explicitly names.

## `scutch.toml`

```toml
[workspace]
name = "my-repo"

[providers.saleor]
mode = "replay"              # replay | live
data = "fixtures/saleor"     # fixture seeds (read-only)
env = "SCUTCH_SALEOR_TOKEN"  # live mode: env var holding the secret
notes = "https://saleor.example/graphql/"

[policy]
require_approval_for = ["plan_apply", "mutate"]
runs_root = ".scutch/runs"
artifacts_root = ".scutch/artifacts"
bootstrap = "Custom bootstrap text for agents…"
```

### Provider modes

| mode | behaviour |
|---|---|
| `replay` | Serves recorded HTTP exchanges from `fixtures/<provider>/*.json`. Deterministic, credential-free, no network. Unknown requests fail loudly. |
| `live` | The secret comes from `env`, or from `resolve = "cmd:pass show saleor"`. It is handed to the worker only. |

Either way, the **handle** is public (`{"provider": "saleor", "resolution":
"runtime"}`) and the **value** is not. `SaleorApi.__repr__` redacts it too, so a
stray log line cannot leak it.

## Operations

```python
@operation(
    name="domain.thing.action",       # dotted, lowercase — validated
    summary="One line for the catalogue.",
    effect="read" | "plan_apply" | "mutate",
    credentials={"api": Provider("svc")},
    idempotency_key=["customer_id", "permission"],   # must name real params
    approval="required",              # default for consequential effects
    external_reads=["svc:resource"],
    intended_effects=["svc:mutation"],
    artifacts=["diagnostics"],        # names ctx.artifact() may write
    examples=[{"name": "...", "why": "...", "input": {...}, "expect": {...}}],
    deprecated=None,
    replaced_by=None,
)
def action(customer_id: CustomerId, *, api) -> Report:
    ...
```

**Contract rules enforced at registration and by `scutch validate`:**

- `name` matches `[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+`.
- The return type is a dataclass — it becomes the public result schema.
- Consequential effects (`plan_apply`, `mutate`) may not declare
  `approval="none"`, and must declare an idempotency key.
- Every idempotency key entry is a real input parameter.
- Every example uses only real inputs.
- `plan_apply` operations declare a plan function.

### Parameters and schemas

The signature is the schema. Annotations project to JSON Schema draft 2020-12:

| Python | JSON Schema |
|---|---|
| `str` / `int` / `float` / `bool` | `string` / `integer` / `number` / `boolean` |
| `Literal["a","b"]` | `{"enum": [...], "type": "string"}` |
| `list[T]`, `dict[str,T]` | `array` with `items`, `object` with `additionalProperties` |
| `T \| None` | `{"anyOf": [T, {"type": "null"}]}` |
| `Enum` subclass | `enum` of its values |
| dataclass | object with required fields and `additionalProperties: false` |

`Annotated[..., field(...)]` adds `description`, `pattern`, `min`, `max`,
`min_length`, `max_length`, `choices`, `format`, `example`, `sensitive`.

### Results and projection

```python
@dataclasses.dataclass
class RepairReport:
    customer_id: str
    status: Literal["applied", "no_change"]
    raw_response: Annotated[dict, private()] = dataclasses.field(default_factory=dict)
```

`private()` fields are dropped from the envelope, listed by *name* under
`redacted`, and absent from `result_schema`. The worker still sees them.

### Plan / apply

Split the read path from the write path. The plan function answers *what would
change*; the operation function *does* it. The runtime only calls the second
after an approval bound to the first.

```python
@plan_for("saleor.customer.repair")
def plan_repair(customer_id, permission, channel="retail", *, api) -> Plan:
    if already_present(api, customer_id, channel, permission):
        return Plan(changes=[], summary="nothing to do",
                    no_change_result=RepairReport(..., status="no_change"))
    return Plan(changes=[Change(kind="permission.add", value=permission,
                               target=f"customer:{customer_id}/channel:{channel}")])
```

An **empty plan** is a first-class outcome: status `complete`, no approval, and
the apply function is never entered. Use `no_change_result` to return the
observation you just made (it is validated against the result schema).

### Artifacts

```python
def diagnose(..., *, ctx) -> Report:
    ctx.artifact("diagnostics", {"raw": payload})   # must be declared
```

Undeclared artifact names raise a contract error. Artifacts land in
`.scutch/artifacts/<run_id>/` and appear in the envelope as
`{"name", "path", "restricted": true, "bytes"}` — a reference, not content.

## Recordings

```bash
scutch record saleor --endpoint <url> --operation <name> --input '<json>' [--approve]
```

Writes `fixtures/<provider>/<cassette>.json` containing scrubbed request/response
pairs. `--approve` is required when the operation mutates, so recording can never
silently change an environment you did not intend to touch. Re-record whenever a
query changes: replay refuses unknown requests, which is what makes the cassette
a contract rather than a convenience.

## Features (Gherkin)

Behaviour future maintainers must understand goes in `specs/*.feature`. A
feature may cover a family of operations by naming them:

```gherkin
Feature: Customer inspection
  Covers: saleor.customer.list, saleor.customer.inspect

  Scenario: Listing customers
    When I list customers
    Then the envelope status is "complete"
```

Scenarios are executed by `tests/gherkin.py`. Step definitions live there; an
undefined step fails the suite rather than being skipped. Continuations run the
**published `next.argv`** through a real subprocess, so the CLI surface is what
gets proven — not an internal helper.

## `just`

`justfile` recipes are thin aliases. They may build a canonical invocation and
nothing else: no schema, no credential handling, no independent workflow
semantics.

```just
customer-repair customer_id permission channel="retail":
    scutch run saleor.customer.repair \
      --input '{"customer_id":"{{customer_id}}","permission":"{{permission}}","channel":"{{channel}}"}'
```

## Tests

Layer confidence; don't collapse it.

1. **Schema validation** — invocation and projected results
   (`tests/test_contract.py`).
2. **Unit tests** — ordinary operation behaviour.
3. **Fixture-backed contract tests** — external API assumptions; the seller is
   `fixtures/`, plus the provider's own adapter.
4. **Idempotency and retry tests** — `tests/test_lifecycle.py`.
5. **Redaction tests** — credentials and private fields stay outside envelopes.
6. **Gherkin scenarios** — `tests/gherkin.py`.

## Generated docs

```bash
scutch docs        # writes docs/generated/*.md from descriptors
```

Never hand-edit `docs/generated/`. If a page is wrong, the descriptor is wrong.
