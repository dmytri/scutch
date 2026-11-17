Feature: One plan can cover several orders

  A fleet-wide change still goes through the same gate: one plan, one digest,
  one approval. What the plan reports is the whole set of changes, so the
  approver sees the blast radius before agreeing to it.

  Covers: orders.bulk

  Scenario: The plan lists every outstanding order
    Given a fresh workspace
    When I run orders.bulk with input {}
    Then the envelope status is "needs_approval"
    And there are 2 changes

  Scenario: Approving the plan fulfils them all at once
    Given a fresh workspace
    When I run orders.bulk with input {}
    And I follow the published next argv
    Then the envelope status is "complete"
    And the result field "remaining" is "0"

  Scenario: Nothing outstanding means no approval at all
    Given a fresh workspace
    When I run orders.bulk with input {}
    And I follow the published next argv
    And I run orders.bulk with input {}
    Then the envelope status is "complete"
    And no approval is required
