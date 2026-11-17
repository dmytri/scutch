Feature: Repeating the same intent does not repeat the effect

  An idempotency key binds one invocation to one effect. Re-sending the same
  request must not mutate twice — and the runner records that, so the answer
  survives a restart rather than depending on anything in memory.

  Covers: orders.inspect

  Scenario: A fulfilled order stays fulfilled when re-inspected
    Given a fresh workspace
    When I run orders.inspect with input {"order_id": "ord_a2"}
    Then the envelope status is "complete"
    And the result field "status" is "fulfilled"

  Scenario: An order that does not exist is refused with a usable hint
    Given a fresh workspace
    When I run orders.inspect with input {"order_id": "ord_zz"}
    Then the envelope status is "failed"
    And the error code is "not_found"
    And the error suggests what to do next

  Scenario: An id that does not match the contract is refused before any call
    Given a fresh workspace
    When I run orders.inspect with input {"order_id": "nonsense"}
    Then the envelope status is "failed"
    And the error code is "invalid_input"
