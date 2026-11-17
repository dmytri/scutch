# Scutch workspace aliases.
#
# These are *thin aliases only*: each recipe builds a canonical `scutch`
# invocation and nothing else. No schema, credential handling or workflow
# semantics may live here — `scutch describe` is the contract.

default:
    @just --list

# --- discovery --------------------------------------------------------------

# Discover the workspace: policies, providers, operation catalogue.
context:
    @scutch context --format json

# List operations.
list:
    @scutch list --format json

# Describe one operation (full contract, schemas, continuation rules).
describe operation:
    @scutch describe {{operation}} --format json

# Check the workspace: contracts, conventions, providers, run store.
doctor:
    @scutch doctor --format json

# --- execution --------------------------------------------------------------

# Read: list orders.
orders:
    @scutch run orders.list --input '{}' --format json

# Read: list only what still needs doing.
outstanding:
    @scutch run orders.list --input '{"status":"placed"}' --format json

# Read: inspect one order.
order order_id:
    @scutch run orders.inspect \
      --input '{"order_id":"{{order_id}}"}' \
      --format json

# Preview fulfilling one order. Returns a plan digest; applying needs approval.
fulfil order_id:
    @scutch run orders.fulfil \
      --input '{"order_id":"{{order_id}}"}' \
      --format json

# Approve and apply an exact plan. The digest comes from the preview.
fulfil-apply order_id approve_plan:
    @scutch run orders.fulfil \
      --input '{"order_id":"{{order_id}}"}' \
      --idempotency-key fulfil-{{order_id}} \
      --approve-plan {{approve_plan}} \
      --format json

# Preview fulfilling every outstanding order, in one plan.
fulfil-all:
    @scutch run orders.bulk --input '{}' --format json

# Inspect a job or run.
status job:
    @scutch status {{job}} --format json

# Cancel a pending plan (its digest stops being approvable; nothing was applied).
cancel job:
    @scutch cancel {{job}} --format json

# Recent local runs.
runs:
    @scutch status --format json

# --- workflows --------------------------------------------------------------

# Run a feature as a workflow: scenarios in order, stopping at the first failure.
workflow feature:
    @scutch workflow {{feature}} --format json

# Show a workflow's steps without running them.
steps feature:
    @scutch workflow {{feature}} --list --format json

# --- interface & maintenance ------------------------------------------------

# Human interface: the same transitions, as guided screens.
ui:
    @scutch ui

# Run the test suite.
test:
    @PYTHONPATH=. python -m pytest tests -q

# Reset local run history and the ledger working copy (the seed is untouched).
reset:
    @rm -rf .scutch/runs/* .scutch/ledger.json
    @echo "run history reset; fixtures/ untouched"
