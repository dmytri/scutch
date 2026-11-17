# Thin aliases. Nothing here is schema or logic: `scutch` is the contract.
default:
    @just --list

# What can I call?
list:
    @scutch list

# How do I call it?
describe op:
    @scutch describe {{op}}

# Read the ledger.
orders:
    @scutch run orders.list

# What still needs doing?
outstanding:
    @scutch run orders.list --input '{"status":"placed"}'

# Preview fulfilling one order. Prints a digest; nothing is written.
fulfil id:
    @scutch run orders.fulfil --input '{"order_id":"{{id}}"}'

# Apply exactly the plan you previewed.
apply id digest:
    @scutch run orders.fulfil --input '{"order_id":"{{id}}"}' --approve {{digest}}

# Preview, then apply, in one go — the shell does the two steps.
do id:
    #!/usr/bin/env bash
    set -euo pipefail
    digest="$(scutch run orders.fulfil --input "{\"order_id\":\"{{id}}\"}" | jq -r .digest)"
    scutch run orders.fulfil --input "{\"order_id\":\"{{id}}\"}" --approve "$digest"

# Fulfil everything outstanding, as one plan.
fulfil-all:
    @scutch run orders.fulfil_all

# Run the tests.
test:
    @python -m pytest tests -q

# Start over: the seed is never modified, so this is always safe.
reset:
    @rm -f ledger.json
    @echo "ledger reset from the committed seed"
