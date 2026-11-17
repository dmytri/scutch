Feature: Steps run in-process, so their seams are assertable
  A step is a Poe script task, not a shell string. That is what lets a feature
  assert exactly which CLI a step calls — with `sh` the argv would be re-joined
  into a string and this would degrade to a grep.

  Covers: saleor.gmv.compute

  Scenario: A GMV step quotes a figure with the inputs that produced it
    Given a fresh workspace
    When I compute net-confirmed GMV of 2967349.6045842674 at 0.8850600770800272
    Then the result is 2232340.27
    And the result names the query file it ran

  Scenario: A step with a missing input is refused, not defaulted
    Given a fresh workspace
    When I compute api-derived GMV without requests
    Then the error names "requests"
