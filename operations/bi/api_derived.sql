-- api-derived GMV  (docs/gmv-basis.md §3)
--
--   ourGmv = recordedRequests ÷ apiRequestsPerGmvDollar
--
-- For customers whose orders never reach Saleor: GMV is inferred from the API
-- usage the contract prices against. `R` defaults to 50.

SELECT
    getvariable('requests')               AS recorded_requests,
    getvariable('requests_per_dollar')    AS requests_per_dollar,
    round(getvariable('requests') / getvariable('requests_per_dollar'), 2) AS our_gmv
;
