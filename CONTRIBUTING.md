# Contributing

Scutch is noncommercial, open source and repository-centred. Its conventions
should be understandable to a future contributor or a fresh agent session
without relying on any previous conversation.

## Before you start

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e . pytest pytest-asyncio
just doctor    # is every documented capability servable?
just test      # 80 tests, ~15s
```

## Adding an operation

1. Write the function first. Ordinary typed Python; no framework types in the
   signature except the provider handle.
2. Declare the contract with `@operation(...)`. If your operation changes
   anything, add `effect="plan_apply"` and a `@plan_for(...)` function that
   returns a `Plan`. Keep the read path and the write path separate.
3. Add an example to `examples=[...]` for each interesting outcome — including
   the no-change case.
4. Write the Gherkin scenarios in `specs/`. They are executed, not decorative.
   A scenario that documents a continuation must follow `next.argv`.
   Say what the operation does *to the outside world* ("sends exactly one
   mutation calling X with these variables"), not just that it returned a
   status — that is what makes a dry run meaningful. A scenario is a statement
   about your workflow against a recorded environment; it is not evidence the
   real API behaves as recorded.
5. Run `just validate` and `just doctor`.
6. Add a `just` alias only if a human will type it repeatedly. The alias builds
   a canonical invocation and nothing more.

Read [`docs/workspace.md`](docs/workspace.md) for the full guide.

## Changing the core

- **Schemas are derived, never written.** If a schema is wrong, the annotation or
  the descriptor is wrong.
- **One lifecycle.** The TUI must call the same `runner.execute` the CLI calls.
  If you need a transition the CLI cannot express, the design is incomplete.
- **No hidden state.** Anything a caller needs to continue must be in the
  envelope. Run records are for inspection and duplicate prevention, not
  context.
- **Never widen what reaches a caller.** New result fields are public by
  default; that is why `private()` exists. Add a redaction test when you add a
  payload-carrying field.
- **Specs before refactors.** If you are changing a behaviour maintainers rely
  on, there should already be a scenario for it. If there is not, write one
  against current behaviour first.

## Test discipline

Layer confidence: schema validation → unit → fixture-backed contract →
idempotency/retry → redaction → Gherkin → acceptance. Do not replace a lower
layer with a higher one.

`tests/test_acceptance.py` is the contract with reality: a fresh agent with no
history and no credentials must be able to do the whole job. If your change
makes that test need a new exception, the interface changed and the brief needs
revisiting.

## Style

Python 3.11+, type annotations everywhere, dataclasses for results, no runtime
magic beyond the decorator. Prefer a clear error with a `hint` over a clever
fallback. Keep stdout clean JSON and put human text on stderr.
