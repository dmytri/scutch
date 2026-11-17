-- charged-plus-offplatform GMV  (docs/gmv-basis.md §3)
--
--   ourGmv = charged + max(0, net × confirmation − charged) × offPlatformShare
--
-- For both faults at once: orders that never happened inflating gross, and real
-- orders settling off-platform deflating charged. `p` is the share of the gap
-- attributed to real off-platform money rather than data error.

SELECT
    round(
        getvariable('charged')
        + greatest(
            0,
            getvariable('net') * getvariable('confirmation') - getvariable('charged')
          ) * getvariable('off_platform_share'),
        2
    ) AS our_gmv
;
