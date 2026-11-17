# Scutch

**A local-first CLI that turns a repository of ordinary scripts into a
self-describing catalogue of typed operations.**

Agents discover operations, supply validated arguments, invoke them, and receive
explicit instructions for continuing. Humans get forms, previews, approvals and
run inspection through a TUI. `just` provides familiar aliases and simple
composition.

There is no account, no registry, no cloud service, no generated framework and
no daemon. The operation contract *is* the product.

```console
$ scutch context --format json     # discover the workspace
$ scutch describe saleor.customer.repair --format json
$ scutch run saleor.customer.repair --input '{"customer_id":"cust_456","permission":"read_orders"}'
{
  "kind": "scutch.envelope/v1",
  "status": "needs_approval",
  "plan": {"changes": [{"kind": "permission.add", "value": "read_orders"}],
           "digest": "sha256:3f13f66…"},
  "next": {"argv": ["scutch","run","saleor.customer.repair","--input","@-", …,
                    "--approve-plan","sha256:3f13f66…"],
           "input": {"customer_id":"cust_456","permission":"read_orders","channel":"retail"}}
}
```

## Why

Exploratory work becomes reusable deterministic operations. That is the useful
idea. Everything else — accounts, registries, generated clients, workflow DSLs —
is not required to get it, and gets in the way of a repository that future
contributors and fresh agent sessions can read.

## Design principles

1. **The operation contract is the product.** Not the CLI framework, workflow
   engine or generated documentation.
2. **No implicit session state.** A fresh process continues from the returned
   arguments alone.
3. **One lifecycle, two interfaces.** The TUI and the JSON interface perform
   identical transitions — they call the same functions.
4. **Ordinary programs remain authoritative.** Business logic stays normal
   Python; Scutch does not swallow it into a DSL.
5. **Self-description is executable truth.** Schemas, invocation instructions
   and docs come from the implementation contract. Drift fails validation.
6. **Minimise what enters model context.** Credentials resolve behind provider
   references; results are deliberately projected, never raw logs.
7. **Prefer established standards.** Python, JSON Schema, JSON/JSONL, Gherkin,
   `just`, and conventional tests.

## Install

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e .            # provides the `scutch` command
```

Optional: `just` for the alias recipes (`apt install just`, `brew install just`,
or grab a release binary).

## Quick start

```bash
scutch context                 # what is this workspace?
scutch list                    # what can I call?
scutch describe saleor.customer.repair
scutch examples saleor.customer.repair
scutch doctor                  # is every documented capability servable?
```

Read-only operations need no approval:

```bash
scutch run saleor.customer.list --input '{}'
```

## Lifecycle

Every response is an envelope. `status` is one of
`needs_input`, `needs_approval`, `running`, `partial`, `complete`, `failed`,
`cancelled`. `next` is always a complete continuation: canonical inputs plus an
`argv`.

| transition | what the caller does |
|---|---|
| `needs_input` | add the listed missing fields to `next.input`, run `next.argv` |
| `needs_approval` | run `next.argv` as given — it already carries `--approve-plan <digest>` |
| `complete` | nothing; `next.argv` inspects the recorded job |
| `partial` | `next.recommended` gives `status` and `recover` commands |
| `cancelled` | `scutch cancel <job>` abandoned the plan; its digest is dead |
| `failed` | fix the reported condition and retry the same invocation |

There is no `scutch continue` and no opaque session token. Approvals are bound
to a digest derived from the operation, the canonical inputs, the idempotency
value, and the ordered changes — a plan that differs in any byte has a different
digest.

## Writing an operation

```python
from scutch import Change, Plan, Provider, operation, plan_for

@operation(
    name="saleor.customer.repair",
    summary="Ensure a customer has one channel permission.",
    effect="plan_apply",
    credentials={"api": Provider("saleor")},
    idempotency_key=["customer_id", "permission", "channel"],
)
def repair_customer(customer_id: CustomerId, permission: Permission,
                    channel: Channel = "retail", *, api) -> RepairReport:
    ...   # ordinary Python

@plan_for("saleor.customer.repair")
def plan_repair(customer_id, permission, channel="retail", *, api) -> Plan:
    ...   # read path: returns Plan(changes=[...])
```

The signature supplies parameters, defaults and JSON Schema; `Annotated[...,
field(...)]` adds constraints; `Annotated[..., private()]` keeps a result field
out of every envelope. `scutch validate` fails if the implementation and the
declared contract disagree.

See [`docs/workspace.md`](docs/workspace.md) for the full authoring guide and
[`docs/agent.md`](docs/agent.md) for the agent-facing boundary.

## Layout

```text
justfile                  thin aliases around canonical invocations
scutch.toml               providers + policy
operations/**/*.py        ordinary typed functions, registered by decorator
providers/*.py            the seam to an external service
specs/*.feature           executable Gherkin for behaviours to preserve
fixtures/<provider>/      recorded HTTP exchanges, one cassette per scenario
docs/generated/           regenerated from descriptors — never hand-edited
.scutch/runs/             inspectable run records
.scutch/artifacts/        run artifacts (restricted by default)
```

Deleting `.scutch/` removes history and the idempotency cache. It never removes
the information needed to formulate an invocation.

## Agent skill

The repo ships a [skills](https://github.com/vercel-labs/skills)-format skill so an
agent can be taught the protocol rather than discovering it by trial:

```bash
npx skills add dmytri/scutch          # installs skills/scutch into your agent
```

It covers discovery, the envelope/continuation protocol, plan-digest approval,
idempotency and recovery, and the authoring rules — with
`skills/scutch/references/envelopes.md` as the full status/error reference.
The skill is a *guide to the same contract*: it adds no capabilities, and
`scutch describe` remains authoritative for any individual operation.

## Recordings, not fixtures

Contract tests replay HTTP, not an application model. `scutch record` runs an
operation against a real endpoint once and writes the request/response pairs to
`fixtures/<provider>/<name>.json`; `mode = "replay"` then serves them back with
no network and no token.

```bash
scutch record saleor --endpoint https://shop.example/graphql/ \
  --operation saleor.customer.repair --input '{"customer_id":"cust_456","permission":"read_orders"}' --approve
```

Why at the socket rather than the provider: doubling the *provider* would stub
out the adapter, which is exactly where the API assumptions live — query text,
variable names, error mapping. A test that fakes the adapter cannot fail when the
GraphQL document is wrong. Replay keeps the document real and only replays the
response.

Consequences worth knowing:

- **One cassette per scenario.** A merged directory lets one scenario's requests
  shadow another's; `SCUTCH_REPLAY=<name>` binds a scenario to one recording.
- **An unknown request fails loudly** — never falls back to a plausible answer.
  A changed query is a test failure, not a green run.
- **Preconditions come from the recording.** `Given customer "x" has ...` reads
  the cassette, so a scenario states what the API was believed to hold.
- **An empty plan records no mutation**, because the runtime never enters the
  apply function in that case.

What replay cannot prove: rate limits, partial failures, eventual consistency,
auth expiry. Those need a live sandbox run.

## Reproducibility

An envelope is a function of the invocation. Same operation, same canonical
input ⇒ same envelope, apart from `run_id` (the attempt) and `timing`.

`job_id` is *derived* from the invocation rather than random, which is what
makes a step's stdout diffable, hashable and cacheable. Without it, every run of
every step produces a unique artifact and "did this run change anything?" has no
answer.

```bash
scutch verify saleor.customer.list --input '{}'
# {"reproducible": true, "envelope_digest": "sha256:59a1f9ef…"}
```

This is about the *workflow*, not the source. A query returning different rows
tomorrow is a new input, not nondeterminism. Pin what varies — an `as_of`, a
window, a cost budget — and the decision over it stays reproducible.

`scutch doctor` checks reproducibility for every operation that declares an
example, so a field carrying a clock or a random id fails there rather than
silently making every artifact unique.

## What the specs prove

A feature states what the workflow *does to the outside world* and how the
caller continues. It fails when that drifts — a dropped mutation, a plan that
claims work it will not do, a mistyped query.

It does not verify the API underneath. Measured: falsifying every recorded email
passes 19/19 scenarios; removing the `Authorization` header passes too. Specs
assert the workflow against a recorded environment, not the service's real
behaviour — that needs a live recording and a reviewed diff.

See [`docs/workspace.md`](docs/workspace.md#what-a-feature-proves--and-what-it-does-not).

## Verification

```bash
pytest            # 96 tests: contract, lifecycle, Gherkin, TUI, acceptance
scutch validate   # contracts and specs agree
scutch doctor     # every documented capability is servable
```

The acceptance test (`tests/test_acceptance.py`) is the decisive one: a
simulated fresh agent with **no conversation history, no credentials and only
the bootstrap text** discovers an operation, resolves missing input, approves an
exact digest, survives a process restart, and is refused when it tries to mutate
twice. Every continuation it performs is the envelope's published `next.argv`.

## Non-goals (v1)

No login, cloud control plane or telemetry. No hosted extension registry. No
general DAG or expression language. No distributed scheduler or required
daemon. No durable-execution competitor. No automatic claim that arbitrary
operations are idempotent. No proprietary workflow serialisation format. No
requirement for agents during unattended execution.

If a workflow later needs distributed retries or durable resumption, a real
engine can invoke Scutch operations. The typed catalogue stays useful underneath.

## Licence

MIT.
