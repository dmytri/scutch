Feature: Customer permission repair
  The operation ensures a customer holds exactly one channel permission.
  It is a plan/apply operation: nothing is mutated until an approval bound to
  the plan digest arrives.

  Scenario: Repeating an already-applied repair
    Given customer "cust_123" already has permission "read_orders" on channel "retail"
    When I preview the permission repair
    Then the envelope status is "complete"
    And the result status is "no_change"
    And no approval is required
    And no mutation is requested

  Scenario: A missing permission requires approval bound to a digest
    Given customer "cust_456" is missing permission "read_orders" on channel "retail"
    When I preview the permission repair
    Then the envelope status is "needs_approval"
    And there is exactly one change
    And the change kind is "permission.add"
    And the next argv approves the returned digest
    And no mutation has happened

  Scenario: The published next argv applies the plan exactly once
    Given customer "cust_456" is missing permission "read_orders" on channel "retail"
    When I preview the permission repair
    And I follow the published next argv
    And I repeat the original invocation unchanged
    Then the envelope status is "complete"
    And the result status is "applied"
    And the second response reports a replay
    And the customer holds exactly one "read_orders" permission on "retail"

  Scenario: An approval for a different plan is refused
    Given customer "cust_456" is missing permission "read_orders" on channel "retail"
    When I preview the permission repair
    And I approve digest "sha256:0000"
    Then the envelope status is "needs_approval"
    And the error code is "approval_mismatch"
    And no mutation has happened

  Scenario: Missing input returns a completed invocation template
    When I run the permission repair with no customer
    Then the envelope status is "needs_input"
    And the error lists "customer_id" as missing
    And the next block carries canonical inputs and an argv

  Scenario: A fresh process continues from the envelope alone
    Given customer "cust_456" is missing permission "read_orders" on channel "retail"
    When I preview the permission repair
    And the process restarts
    And I follow the published next argv
    Then the envelope status is "complete"
    And the result status is "applied"

  Scenario: A cancelled plan cannot be approved
    Given customer "cust_456" is missing permission "read_orders" on channel "retail"
    When I preview the permission repair
    And I cancel the pending plan
    And I approve the cancelled digest
    Then the envelope status is "cancelled"
    And the error code is "plan_cancelled"
    And no mutation has happened
