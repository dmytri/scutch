# Envelope reference

One shape for every response:

```json
{
  "kind": "scutch.envelope/v1",
  "operation": "saleor.customer.repair",
  "status": "needs_approval",
  "input": { "customer_id": "cust_456", "permission": "read_orders", "channel": "retail" },
  "plan": { "changes": [{ "kind": "permission.add", "target": "...", "value": "read_orders" }],
            "digest": "sha256:3f13f66…", "summary": "grant read_orders …" },
  "result": null,
  "redacted": [],
  "artifacts": [],
  "error": null,
  "next": { "argv": ["scutch", "run", "…", "--approve-plan", "sha256:3f13f66…"],
            "input": { "…": "…" }, "plan_digest": "sha256:3f13f66…" },
  "notes": [],
  "run_id": "run_20261116T211324Z_b8c0591b",
  "job_id": "job_21073e24596b"
}
```

`input` is always the **canonical** input: defaults filled in, so it reproduces
the call exactly. `next` is always present and always complete.

## Statuses

### `needs_input`
`error.code = "missing_input"`, `error.details.missing` is the field list,
`error.details.schema` is a schema for *just* the missing fields.

```json
{"status": "needs_input",
 "input": {"channel": "retail"},
 "error": {"code": "missing_input", "message": "required inputs are missing",
           "details": {"missing": ["customer_id", "permission"], "schema": {...}}},
 "next": {"argv": ["scutch","run","saleor.customer.repair","--input","@-","--format","json"],
          "input": {"channel": "retail"}}}
```

Merge the missing fields into `next.input` and run `next.argv`.

### `needs_approval`
Nothing has been applied. `plan.changes` is the exact intended effect;
`plan.digest` binds it. `next.argv` already includes `--approve-plan <digest>`.

`error.code = "approval_mismatch"` (exit 5) means a digest was supplied but the
plan differs — the body carries the *current* digest. Nothing was applied.

### `complete`
`result` is the projected output (private fields removed, listed under
`redacted`). `next.argv` inspects the recorded job.

A no-change plan also lands here, with `plan.changes == []` and no approval.
A replay lands here too, with `replay.mutation_requested: false`:

```json
{"replay": {"of_run": "run_…", "recorded_status": "complete",
            "mutation_requested": false,
            "note": "this invocation already took effect; no new mutation was issued"}}
```

### `cancelled`
The plan was cancelled before approval, so nothing was applied and its digest is
dead. Cancelling is the honest way to abandon a plan:

```bash
scutch cancel job_21073e24596b          # from next.cancel on a planned status
```

Approving that digest later returns `cancelled` with `error.code =
"plan_cancelled"` (exit 10) and `next.argv` pointing at a fresh preview.
Cancelling an applied consequence is refused with `already_applied` (exit 11);
cancelling something with no pending plan is `not_cancellable` (exit 12).

### `partial`
An apply was interrupted (process killed mid-flight). `next.recommended` holds
`["scutch","status",job]` and `["scutch","recover",run]`. Verify the external
state before repeating anything.

### `failed`
`error.code` is one of:

| code | exit | meaning |
|---|---|---|
| `invalid_input` | 4 | schema violations in `error.details.violations` |
| `idempotency_conflict` | 9 | key reused with different inputs (`error.details.existing_input`) |
| `in_flight` | 8 | identical invocation recorded but unfinished |
| `approval_mismatch` | 5 | (status stays `needs_approval`) |
| `plan_cancelled` | 10 | (status `cancelled`) the digest belonged to a cancelled plan |
| `already_applied` | 11 | (status `complete`) cannot cancel what took effect |
| `not_cancellable` | 12 | run has no pending plan |
| `provider_not_configured` | 6 | provider missing from `scutch.toml` |
| `provider_error` | 6 | provider resolution or call failed |
| `not_found` | 3 | unknown operation or entity; `details` often lists valid ids |
| `operation_failed` | 7 | the operation raised; `error.hint` says what to do |

Exit code 0 covers `complete`, `needs_input` and `needs_approval` — those are
successful transitions, not errors. Branch on `status`, not on the exit code.

## Redaction and artifacts

```json
{"result": {"customer_id": "cust_456", "status": "applied"},
 "redacted": ["raw_response"],
 "artifacts": [{"name": "diagnostics", "path": ".scutch/artifacts/run_…/diagnostics.json",
                "restricted": true, "bytes": 4021}]}
```

`redacted` names what was withheld — you learn *that* something exists, never
its value. Artifacts are references. Do not open them unless the user asks and
hands you the path.

## Continuation rules

- Run `next.argv` verbatim. `next.input` is what to pipe into its stdin.
- There is no `scutch continue` and no opaque token. Any process can pick up the
  envelope and continue.
- `scutch status <job_id>` returns `scutch.status/v1` with `status`, `phase`,
  `input`, `plan_digest`, `result` and its own `next` — including the approval
  command for a plan that is still `planned`.
