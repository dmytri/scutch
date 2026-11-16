# The agent-facing boundary

The security property Scutch provides is **not secret encryption**. It is
keeping credentials and unnecessary raw data out of repeated LLM exchanges.

## Bootstrap

A fresh session needs only this:

```markdown
Run `scutch context --format json` to discover this repository.
Run `scutch describe NAME --format json` before invoking an operation.
Follow the returned `next.argv` and `next.input` exactly.
Never request credential values or treat restricted artifacts as agent input.
Mutations require approval bound to the returned plan digest.
```

That text is returned by `scutch context` under `bootstrap` (configurable under
`[policy]` in `scutch.toml`), so it cannot drift from the interface.

## What an agent receives

| receives | never receives |
|---|---|
| provider handles (`{"provider":"saleor","resolution":"runtime"}`) | credential values |
| typed operation descriptions and JSON Schemas | unresolved provider internals |
| validated canonical arguments | other callers' inputs |
| deliberately projected results | raw API payloads, logs, stdout |
| references to declared artifacts | artifact contents |
| structured errors with remediation hints | stack traces in the envelope |

## What a worker receives

The resolved provider, network access, raw responses, and unrestricted
diagnostics. The boundary is between *worker* and *caller*, not between Scutch
and the machine.

> **If an agent also has unrestricted filesystem, environment and log access,
> Scutch is not a security sandbox.** The boundary depends on constraining the
> agent's available tools. Scutch's contribution is that the *documented* path
> never requires a secret to cross the conversation.

## Continuations, not sessions

Every envelope carries a complete `next` block. There is no `scutch continue`
and no opaque session token, so:

- a process can be restarted at any point without losing the ability to
  continue;
- two agents can cooperate by passing one envelope;
- an agent's context can be trimmed to the envelope alone.

Approval is bound to a digest over the operation name, canonical inputs,
idempotency value and ordered changes. Passing `--approve-plan` for anything
else yields `approval_mismatch` with the current digest and nothing applied.

## Failure shapes

| code | meaning | what to do |
|---|---|---|
| `missing_input` | required fields absent | fill from `error.details.schema`, rerun `next.argv` |
| `invalid_input` | schema violations | `error.details.violations` names the field and validator |
| `approval_mismatch` | digest stale or wrong | approve the digest in the response; nothing was applied |
| `idempotency_conflict` | key reused with different inputs | new key for a new intent |
| `in_flight` | identical invocation never finished | `next.recommended` → `status`, then `recover` |
| `provider_not_configured` | provider missing from `scutch.toml` | operator action |
| `not_found` | unknown operation or entity | `error.details` often lists valid ids |
| `operation_failed` | the operation raised | `error.hint` says how to proceed |

## Repetition and crash recovery

- An **explicit** `--idempotency-key` promises: the same key with the same
  canonical inputs returns the recorded result with `replay.mutation_requested
  = false`; the same key with different inputs is a hard error.
- Without an explicit key, Scutch derives one from the canonical inputs, so an
  accidental double-click cannot double-mutate.
- A run interrupted mid-apply leaves `phase: applying`. `scutch status` then
  returns recovery commands; `scutch recover` reports the recorded intent.
  Neither repeats the effect — verify external state first.

## MCP and other projections

`scutch describe NAME` is sufficient to generate an MCP tool definition, a
generated doc page, or a TUI form. They are all projections of one registry.
No second schema system is introduced.
