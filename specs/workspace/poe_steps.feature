Feature: Steps run in-process, so their seams are assertable

  A step is a Poe script task, not a shell string. That is what lets a feature
  assert exactly which CLI a step calls — with `sh` the argv would be re-joined
  into a string, and the assertion would degrade to a grep.

  Covers: orders.list

  Scenario: A read step reports the catalogue without planning anything
    Given a fresh workspace
    When I run orders.list
    Then the envelope status is "complete"
    And no approval is required
    And the result field "count" is "4"

  Scenario: A filtered read step narrows the same operation
    Given a fresh workspace
    When I run orders.list with input {"status": "placed"}
    Then the envelope status is "complete"
    And the result field "count" is "2"
