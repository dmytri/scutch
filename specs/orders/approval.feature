Feature: A mutation waits for approval bound to a plan

  This is the property the runner exists to guarantee. A consequential operation
  does not mutate on the first call: it returns a plan, the caller approves that
  exact plan by its digest, and only then is anything written.

  Covers: orders.fulfil

  Scenario: Previewing plans the change and writes nothing
    Given a fresh workspace
    When I run orders.fulfil with input {"order_id": "ord_a1"}
    Then the envelope status is "needs_approval"
    And there is exactly one change
    And the change kind is "status.change"
    And the next argv approves the returned digest

  Scenario: Following the published argv applies exactly that plan
    Given a fresh workspace
    When I run orders.fulfil with input {"order_id": "ord_a1"}
    And I follow the published next argv
    Then the envelope status is "complete"
    And the result field "status" is "fulfilled"

  Scenario: An already-fulfilled order needs no approval
    Given a fresh workspace
    When I run orders.fulfil with input {"order_id": "ord_a2"}
    Then the envelope status is "complete"
    And no approval is required
    And no mutation is requested

  Scenario: A cancelled order is left alone
    Given a fresh workspace
    When I run orders.fulfil with input {"order_id": "ord_c1"}
    Then the envelope status is "complete"
    And no approval is required
    And no mutation is requested
