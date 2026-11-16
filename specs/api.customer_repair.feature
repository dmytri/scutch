Feature: Customer repair over the API
  Each scenario replays one recorded HTTP session, so it can say what would be
  sent. The transport is real — GraphQL text, variable names, error mapping —
  and only the socket is replayed. A dry look here is therefore a claim about
  the request, not about our own bookkeeping.

  Scenario: Previewing prepares the request and sends no mutation
    Given the recording "scenario-repair"
    And customer "cust_456" is missing permission "read_orders" on channel "retail"
    When I preview the permission repair
    Then the envelope status is "needs_approval"
    And the plan intends one mutation of kind "permission.add"
    And the request log holds no mutation
    And the request log holds a read of "cust_456"

  Scenario: Approval sends exactly one mutation with the documented variables
    Given the recording "scenario-repair"
    And customer "cust_456" is missing permission "read_orders" on channel "retail"
    When I preview the permission repair
    And I follow the published next argv
    Then the envelope status is "complete"
    And the result status is "applied"
    And the request log holds exactly 1 mutation
    And that mutation calls "channelUpdatePermission"
    And that mutation sends id "cust_456", channel "retail" and permission "read_orders"
    And no credential appears in the recorded exchanges

  Scenario: Repeating an approved invocation sends no further mutation
    Given the recording "scenario-repair"
    And customer "cust_456" is missing permission "read_orders" on channel "retail"
    When I preview the permission repair
    And I follow the published next argv
    And I repeat the original invocation unchanged
    Then the envelope status is "complete"
    And the request log holds exactly 1 mutation

  Scenario: A repair already satisfied sends no mutation at all
    Given the recording "scenario-nochange"
    And customer "cust_123" already has permission "read_orders" on channel "retail"
    When I preview the permission repair
    Then the envelope status is "complete"
    And the result status is "no_change"
    And the request log holds no mutation

  Scenario: A request that was never recorded is a failure, not a silent pass
    Given the recording "scenario-inspect-123"
    When I request an operation whose query is not recorded
    Then the envelope status is "failed"
    And the error code is "provider_error"
    And the hint says to re-record the fixture
