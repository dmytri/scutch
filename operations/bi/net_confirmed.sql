-- net-confirmed GMV  (docs/gmv-basis.md §3, default basis)
--
--   ourGmv = net × confirmation × (1 − returnRate)
--
-- A query is a DuckDB artifact before it is an operation: run this by hand to
-- check a number, and let the operation guard the inputs.
--
--   duckdb -c "SET VARIABLE net=2967349.60; SET VARIABLE confirmation=0.88506; \
--              SET VARIABLE return_rate=0.15; $(cat net_confirmed.sql)"
--
-- Verified against the stored account-economics report for Dreamtonics:
-- 2,232,340.27 exactly (docs/gmv-basis.md §3, "net-confirmed").

SELECT
    getvariable('net')            AS platform_net,
    getvariable('confirmation')   AS confirmation,
    getvariable('return_rate')    AS return_rate,
    round(
        getvariable('net')
        * getvariable('confirmation')
        * (1 - getvariable('return_rate')),
        2
    ) AS our_gmv
;
