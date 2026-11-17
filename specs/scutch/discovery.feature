Feature: Discovery
  A fresh session with no conversation history must be able to learn what it
  may call from the workspace alone.

  Scenario: The context names the policy and the catalogue
    When I ask for the workspace context
    Then the bootstrap forbids requesting credential values
    And the catalogue lists "saleor.customer.repair"
    And the policy requires approval for "plan_apply"

  Scenario: Description answers the invocation questions
    When I describe "saleor.customer.repair"
    Then it declares effect "plan_apply"
    And its credentials name only the provider handle "saleor"
    And its input schema requires "customer_id" and "permission"
    And its idempotency key covers "customer_id", "permission", "channel"
    And its source is the implementing module

  Scenario: Every consequence is declared
    When I describe "saleor.customer.repair"
    Then the intended effects name "saleor:channelUpdatePermission"
    And the external reads name "saleor:user.channelPermissions"
