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

`usage.analyse` is what this repo is for: one customer's usage, from two
systems, with a projection.

```
usage.metabase  ─┐
                 ├─▶ usage.analyse ─▶ orders, equivalent requests, SARIMAX
usage.vercel    ─┘
```

```console
$ scutch run usage.analyse --input '{"customer":"acme","project":"acme-app"}'
```

```json
{
  "latest":  {"month": "2026-04", "orders": 8625},
  "mom":     {"orders": -7.61},
  "yoy":     {"orders": 6.64},
  "wow":     {"orders": null, "basis": "unavailable: the source series is monthly"},
  "ttm":     {"orders": 89009, "equivalent_requests": 1281199992,
              "equivalent_requests_per_order_month": 12378.74},
  "hosting": {"formula": "CDN Requests / 6 + ISR Writes / 6 + "
                         "(Fast Data Transfer GB + Fast Origin Transfer GB) x 6,000 "
                         "+ Image Transformations x 3",
              "equivalent_requests": 106766666,
              "components": [ …raw and converted, so the number can be explained… ]},
  "forecast": {"method": "SARIMAX(1,1,1)(1,1,0,12)",
               "next_week":  {"orders": 2249},
               "next_month": {"orders": 9772, "month": "2026-05"},
               "ntm":        {"orders": 103625, "equivalent_requests": 1282746932}}
}
```

**Inputs are analyst exports, not live APIs.** A Metabase saved-question JSON
export and Vercel's FOCUS billing JSONL export. That is deliberate: the number a
customer is billed against comes from the billing export, so reading that file is
more honest than reconstructing it from an API — and it means no credential is
involved anywhere in this workflow.

**`wow` is `null` when the source is monthly**, with the reason attached. A
number there would be invented; dividing a month by 4.345 is an approximation
this report refuses to dress up as a measurement.

### Testing a workflow like this

`tests/test_usage_workflow.py` asserts the **calls**, never the arithmetic:

```python
def test_the_order_is_metabase_then_vercel():
    kinds = [a["kind"] for a in asks_of()]
    assert kinds == ["run", "read", "run", "read"]
```

There is no "TTM equals N" and no "the forecast is positive" — those are
properties of ordinary Python that a person can check by hand. What cannot be
checked by hand is whether we read the right export, with the right customer, in
the right order. That is what the tests do.

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
