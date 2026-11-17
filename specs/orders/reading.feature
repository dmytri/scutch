Feature: Reading an operation without causing an effect

  A read-only operation declares `effect = "read"`. That declaration is a
  promise the runner keeps for the caller: no plan is computed, no approval is
  requested, and the envelope says so rather than leaving it implied.

  Covers: orders.list

  Scenario: Listing orders needs no approval
    Given a fresh workspace
    When I run orders.list
    Then the envelope status is "complete"
    And no approval is required
    And no mutation is requested

  Scenario: A read can be narrowed by its input
    Given a fresh workspace
    When I run orders.list with input {"status": "placed"}
    Then the envelope status is "complete"
    And the result field "count" is "2"
