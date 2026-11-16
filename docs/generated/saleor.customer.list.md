# saleor.customer.list

List customers with their channel permissions.

- **Effect:** read
- **Approval:** none
- **Providers:** `saleor`

## Parameters

| name | type | required | default |
|---|---|---|---|
| `limit` | `integer` | False | 20 |

## Invocation

```bash
scutch run saleor.customer.list --input @- --format json
```

## Behaviour

```gherkin
Feature: Customer inspection
  Read-only operations need no approval and mutate nothing.

  Covers: saleor.customer.list, saleor.customer.inspect

  Scenario: Listing customers
    When I list customers
    Then the envelope status is "complete"
    And the result lists "cust_123"

  Scenario: Inspecting a customer projects only public fields
    When I inspect customer "cust_123"
    Then the envelope status is "complete"
    And the result has no restricted fields
    And the envelope withholds nothing

  Scenario: A repair result withholds its raw provider payload
    Given customer "cust_123" already has permission "read_orders" on channel "retail"
    When I preview the permission repair
    Then the envelope reports a redacted field

  Scenario: An unknown customer is a structured error
    When I inspect customer "cust_999"
    Then the envelope status is "failed"
    And the error code is "not_found"
```

## Source

`scutch_ops_operations_saleor_customer_repair.list_customers`
