-- MoM, QoQ, YoY and TTM over a monthly orders series, in one pass.
--
-- This is the arithmetic behind the report, as a file a person can run. The
-- operation passes the rows in as JSON; nothing here is generated.
--
-- Run it by hand:
--   duckdb -json -c "SET VARIABLE orders='<rows json>';
--                    $(cat operations/usage/queries/windows.sql)"
--
-- Windows are month offsets, so "this month vs last month" is 1, "vs three
-- months ago" is 3, and "vs the same month last year" is 12. TTM is the sum of
-- the trailing 12 months *including* the latest.
WITH o AS (
    SELECT
        (s->>'month')::VARCHAR     AS month,
        (s->>'orders')::BIGINT     AS orders
    FROM unnest(from_json(getvariable('orders'), '["json"]')) AS t(s)
),
ranked AS (
    SELECT *, ROW_NUMBER() OVER (ORDER BY month DESC) AS months_back FROM o
),
latest AS (SELECT * FROM ranked WHERE months_back = 1)
SELECT
    l.month                                                       AS latest_month,
    l.orders                                                      AS latest_orders,
    ROUND(l.orders * 100.0 / NULLIF(m1.orders, 0) - 100, 2)       AS mom_pct,
    ROUND(l.orders * 100.0 / NULLIF(m3.orders, 0) - 100, 2)       AS qoq_pct,
    ROUND(l.orders * 100.0 / NULLIF(m12.orders, 0) - 100, 2)      AS yoy_pct,
    (SELECT SUM(orders) FROM ranked WHERE months_back <= 12)      AS ttm_orders,
    (SELECT COUNT(*) FROM ranked)                                 AS months
FROM latest l
LEFT JOIN ranked m1  ON m1.months_back  = 2
LEFT JOIN ranked m3  ON m3.months_back  = 4
LEFT JOIN ranked m12 ON m12.months_back = 13
