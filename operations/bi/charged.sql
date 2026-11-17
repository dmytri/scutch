-- charged GMV  (docs/gmv-basis.md §3)
--
--   ourGmv = charged × externalPaymentUplift
--
-- For customers whose unconfirmed orders dominate: once half the recorded orders
-- are phantom, every gross-derived figure inherits that error, while charged is
-- observed fact. It is a floor — payment settled outside Saleor is invisible.

SELECT
    getvariable('charged')                 AS platform_charged,
    getvariable('external_payment_uplift') AS uplift,
    round(getvariable('charged') * getvariable('external_payment_uplift'), 2) AS our_gmv
;
