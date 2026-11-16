# Scutch workspace aliases.
#
# These are *thin aliases only*: they build a canonical `scutch` invocation and
# nothing else. No schema, credential handling or workflow semantics may live
# here — `scutch describe` is the contract.

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

# Executable examples for one operation.
examples operation:
    @scutch examples {{operation}} --format json

# Check the workspace: contracts, specs, providers, run store.
doctor:
    @scutch doctor --format json

# --- execution --------------------------------------------------------------

# Read: list customers.
customers:
    @scutch run saleor.customer.list --input '{}' --format json

# Read: inspect one customer.
customer customer_id:
    @scutch run saleor.customer.inspect \
      --input '{"customer_id":"{{customer_id}}"}' \
      --format json

# Preview a permission repair. Returns a plan digest; applying needs approval.
customer-repair customer_id permission channel="retail":
    @scutch run saleor.customer.repair \
      --input '{"customer_id":"{{customer_id}}","permission":"{{permission}}","channel":"{{channel}}"}' \
      --format json

# Approve and apply an exact plan. The digest comes from the preview.
customer-repair-apply customer_id permission channel approve_plan:
    @scutch run saleor.customer.repair \
      --input '{"customer_id":"{{customer_id}}","permission":"{{permission}}","channel":"{{channel}}"}' \
      --idempotency-key repair-{{customer_id}}-{{permission}}-{{channel}} \
      --approve-plan {{approve_plan}} \
      --format json

# Inspect a job or run.
status job:
    @scutch status {{job}} --format json

# Recent local runs.
runs:
    @scutch runs --format json

# --- interface & maintenance ------------------------------------------------

# Human interface: same transitions, forms and approvals.
ui:
    @scutch ui

# Regenerate docs from descriptors.
docs:
    @scutch docs --format json

# Prove implementation and schemas agree, and specs exist.
validate:
    @scutch validate --format json

# Run the test suite.
test:
    @PYTHONPATH=. python -m pytest tests -q

# Reset applied fixture state and local history.
reset:
    @rm -rf .scutch/runs/* .scutch/state
    @echo "fixture state reset; seeds untouched"
