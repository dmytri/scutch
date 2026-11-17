Feature: Discovery

  A fresh session with no conversation history must be able to learn what it may
  call from the workspace alone — and nothing it learns there may require asking
  a human first.

  Covers: orders.fulfil

  Scenario: The context names the policy and the catalogue
    When I ask for the workspace context
    Then the bootstrap forbids requesting credential values
    And the catalogue lists "orders.fulfil"
    And the policy requires approval for "plan_apply"

  Scenario: Description answers the invocation questions
    When I describe "orders.fulfil"
    Then it declares effect "plan_apply"
    And its credentials name only the provider handle "ledger"
    And its input schema requires "order_id"
    And its idempotency key covers "order_id"
    And its source is the implementing module
