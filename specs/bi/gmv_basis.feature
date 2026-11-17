Feature: GMV basis
  GMV is not one number. `docs/gmv-basis.md` defines four bases, and which one a
  figure uses decides whether an allowance is breached. The arithmetic lives in
  a DuckDB file per basis, so the formula is legible and runnable by hand.

  Covers: saleor.gmv.compute, saleor.gmv.bases

  Scenario: Every basis is named with the inputs it requires
    When I run saleor.gmv.bases
    Then the envelope status is "complete"
    And the result lists 4 bases

  Scenario: net-confirmed reproduces the stored report
    When I run saleor.gmv.compute with input {"basis": "net-confirmed", "net": 2967349.6045842674, "confirmation": 0.8850600770800272, "return_rate": 0.15}
    Then the envelope status is "complete"
    And our GMV is 2232340.27

  Scenario: api-derived divides requests by the measured rate
    When I run saleor.gmv.compute with input {"basis": "api-derived", "requests": 4283648.666666667, "requests_per_dollar": 50.0}
    Then the envelope status is "complete"
    And our GMV is 85672.97

  Scenario: An unknown basis is refused with the permitted values
    When I run saleor.gmv.compute with input {"basis": "gross", "net": 100}
    Then the envelope status is "failed"
    And the error names the permitted bases
