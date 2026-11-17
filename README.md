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

```text
operations/**/*.py        the catalogue: one function per file, `io` first
scutch/io.py              the seam
scutch/run.py             plan / apply, over one function
scutch/catalogue.py       discover operations from the folder
scutch/cli.py             list, describe, run
scutch/data/seed.json     demo data; copied to ledger.json, never modified
tests/                    the seam, the lifecycle, the continuation, the CLI
```

There is no registry, no configuration beyond the demo's seed path, no daemon,
no account, no network required for any test.

## Non-goals

No workflow engine, no scheduler, no durable execution. If a job needs
distributed retries, a real engine can call these functions — they are ordinary
Python that happens to take `io`.
