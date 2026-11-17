# Scutch

**A folder of plain functions, and one seam to the world.**

An operation is a Python function that takes `io` first. Every effect it can have
— reading, writing, running a command, calling an HTTP endpoint — goes through
that one object. So the plan is exactly the effects the function asked for, and
approving a plan means approving that list.

```console
$ scutch list                       # what can I call?
$ scutch describe orders.fulfil     # how do I call it?
$ scutch run orders.fulfil --input '{"order_id":"ord_a1"}'
{
  "digest": "sha256:0eaf4259…",
  "effects": ["{\"kind\":\"write\",\"path\":\"ledger.json\",…}"],
  "result": {"changed": true, "order_id": "ord_a1", "status": "fulfilled"},
  "status": "dry"
}
$ scutch run orders.fulfil --input '{"order_id":"ord_a1"}' --approve sha256:0eaf4259…
{"status": "applied", …}
```

## The whole design

One function:

```python
io(kind, **args)          # kind: read | write | exec | http
```

Three modes, and nothing between them:

| | reads | writes, exec, http |
|---|---|---|
| `IO(root)` | the world | happen |
| `IO(root, execute=False)` | the world | recorded only — **this is the plan** |
| `IO(root, answers=[…])` | canned replies | happen |

An operation is called **twice** with the same inputs: once to learn its effects,
once to perform them. The digest of those effects is what approval binds to. If
the effects drift between the two passes, the run is refused.

## A real workflow

A workflow is **an operation that calls operations**. There is no separate YAML,
no DAG file, no scheduler. `operations/usage/analyse.py` is one:

```python
def usage_report(io, customer: str, project: str):
    orders = io("run", operation="usage.metabase", customer=customer)["rows"]
    usage  = io("run", operation="usage.vercel", project=project)["rows"]
    ...            # WoW, MoM, TTM, then SARIMAX
```

`scutch describe usage.analyse` reports what it composes:

```json
{"name": "usage.analyse", "calls": ["usage.metabase", "usage.vercel"]}
```

and it appears in the browser like any other operation. The steps and the code
cannot disagree, because the steps *are* the code.

### What the example does

Cross-reference **Metabase** order counts with **Vercel** API usage for one
customer, then answer the commercial question:

```console
$ scutch run usage.analyse --input '{"customer":"acme","project":"acme-app"}'
{
  "customer": "acme",          "weeks": 78,
  "latest":  {"week_start": "2026-06-29", "orders": 1568,
              "requests": 1138705, "requests_per_order": 726.2},
  "wow":     {"orders": 19.97,  "requests": 9.09},
  "mom":     {"orders": 26.86,  "requests": 23.71},
  "yoy":     {"orders": 26.55,  "requests": 68.22},
  "ttm":     {"orders": 76141,  "requests": 44611168,
              "avg_requests_per_order": 585.9},
  "forecast": {
    "next_week":  {"week_start": "2026-07-06", "orders": 1605, "requests": 1119988},
    "next_month": {"orders": 7363, "requests": 4659609},
    "next_twelve_months": {"orders": 91418, "requests": 67980955},
    "confidence_80_next_week": {"orders": [1430, 1781]}
  }
}
```

78 weeks of history so TTM and a seasonal SARIMAX are meaningful. The model is
`(1,1,1)(1,1,0,52)` — a random walk with drift and an annual cycle. A week
present in one series and not the other is **reported, not zero-filled**: a zero
would look like real usage that did not happen.

### Observations are not effects

`exec` and `read` *observe*; `write` and `http` *act*. Only the second kind is in
the plan. This matters more than it sounds: the Metabase session token comes back
from an `exec` and is sent in a header, so counting it as an effect meant the
token was printed in `effects` and hashed into the digest — an unstable plan that
leaked a credential. Header values are now `<redacted>` in the plan, and a test
asserts the digest is identical across two different session tokens.

## Two front ends, one flow

An agent does four things: **list → describe → run (dry) → run --approve**.
`scutch ui` does the same four in a full-screen browser, built on Textual:

```
┌ Operations ──────────┬──────────────────────────────────────────┐
│ > orders.fulfil      │ Review — nothing has happened yet        │
│   Mark a placed or…  │                                          │
│   orders.fulfil_all  │ This will:                               │
│   Fulfil everything… │   • write ledger.json                    │
│   orders.list        │                                          │
│   List orders        │ digest sha256:0eaf4259…                  │
│   orders.notify      │                                          │
├──────────────────────┴──────────────────────────────────────────┤
│      [ Apply ]  [ Back ]           q quit  esc back             │
└─────────────────────────────────────────────────────────────────┘
```

A read-only operation skips the review screen: its result is already known from
the dry pass, so it goes straight to a `DataTable` you can scroll. Filter it by
filling the form (`status = placed`) and pressing preview.

Colour carries meaning rather than decoration:

| | |
|---|---|
| 🟠 `#fea62b` | a write, and the selection in the rail |
| 🟢 `#4ebf71` | an HTTP effect, and the review header |
| 🟣 `#b48ead` | a command (`exec`) |
| status cells | green fulfilled, yellow placed, red cancelled |

`NO_COLOR=1` in your environment turns all of it off — Textual honours the
standard, so the screen goes monochrome and `console.color_system` reports
`None`. That is deliberate; clear the variable to see colour.

`scutch prompt` is the same flow line by line, for a pipe or a dumb terminal.
Both call `run.plan` and `run.apply` — the same functions the CLI calls — so the
digest on screen is the digest the CLI would print. A test asserts that.

## Why it is built this way

The previous version of this repo modelled what external services *returned* —
response types, recorded cassettes, provider modes — and every model was a guess
about somebody else's API. Six of its guards turned out to be declarations the
runtime never honoured: an approval requirement with no plan to approve, a
`sensitive` flag nothing filtered, a recovery command that did not exist.

This version has no declarations to disobey. There is one door, the plan is the
door's record, and a plan cannot be missing from an operation that mutates
because the plan *is* the operation's effect list.

## Writing an operation

A file in `operations/`. The path is the name.

```python
# operations/orders/fulfil.py  ->  orders.fulfil
import json

def fulfil_order(io, order_id: str):
    """Mark a placed order fulfilled."""
    data = json.loads(io("read", path="ledger.json"))
    order = next(o for o in data["orders"] if o["id"] == order_id)
    if order["status"] != "placed":
        return {"order_id": order_id, "changed": False}
    order["status"] = "fulfilled"
    io("write", path="ledger.json", text=json.dumps(data, indent=2))
    return {"order_id": order_id, "changed": True}
```

The signature is the schema — `describe` reads it. The docstring is the
description. There is nothing else to declare.

## Testing an operation

A test gives the double some answers and asserts **what was asked**, never what
an answer contained:

```python
io = IO(root=ws, answers=[ledger_text, "ok"])
fulfil_and_notify(io, order_id="ord_a1", url="https://hooks.example/x")
assert [json.loads(a)["kind"] for a in io.asks] == ["read", "write", "http"]
```

An answer is a permission slip: it exists so the code can walk to its next ask.
The claim is the sequence of asks. If the code stops asking for something, the
extra answer is still sitting in `io.answers` and the test can see it.

## Layout

Everything, in full. This is the whole repository:

```text
operations/usage/         the real example
  metabase.py             usage.metabase     orders per week (session, then query)
  vercel.py               usage.vercel       requests per week
  analyse.py              usage.analyse      the workflow: both, then WoW/MoM/TTM/SARIMAX

operations/orders/        a smaller demo — a JSON ledger
  list.py                 orders.list        look at the ledger
  fulfil.py               orders.fulfil      one order
  fulfil_all.py           orders.fulfil_all  every placed order, one plan
  notify.py               orders.notify      write + POST, two effects

scutch/
  io.py                   the seam: read / write / exec / http
  run.py                  plan and apply, two passes over one function
  catalogue.py            discover operations from the folder
  cli.py                  list, describe, run, ui, prompt
  browser.py              the Textual browser
  tui.py                  the same flow, line by line
  data/seed.json          the demo's starting data

fixtures/                 weekly series standing in for the two APIs
tests/                    seam, lifecycle, continuation, browser, cli, workflow
```

### Where is `ledger.json`?

It is not in the repo — it is created on first run by copying
`scutch/data/seed.json`, then written to by the operations. It is local state:

```console
$ cat scutch/data/seed.json        # what you start with
$ cat ledger.json                  # what you have now
$ rm ledger.json                   # start over (the seed is untouched)
```

Four orders: `ord_a1` and `ord_b1` are `placed`, `ord_a2` is `fulfilled`,
`ord_c1` is `cancelled`. `orders.fulfil ord_a1` moves the first one.

### There are no `.feature` files

An earlier version of this repo ran Gherkin features as workflows. That layer is
gone: an operation is a function, its tests are ordinary pytest tests in
`tests/`, and the browser is the human interface. If you are looking for
`specs/*.feature`, they were part of the design that was replaced — see the
commit message on `62c20d2`.

## Non-goals

No workflow engine, no scheduler, no durable execution. If a job needs
distributed retries, a real engine can call these functions — they are ordinary
Python that happens to take `io`.

## Running the tests

```console
$ pip install -e '.[dev]'
$ python -m pytest tests -q
```
