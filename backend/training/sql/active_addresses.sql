-- Addresses active in each time-step snapshot (a few blocks every 2016 blocks).
-- Cheap: the table is clustered by block, so only the snapshot blocks are read.
-- Steps before @post_first_step keep every address (a superset of the Elliptic++ wallets);
-- later steps keep a fixed-size sample per step, chosen by a hash so reruns pick the same one.
--
-- Parameters: @steps ARRAY<STRUCT<step INT64, snap_start INT64, snap_end INT64>>,
--   @month_lo DATE, @month_hi DATE, @base_block INT64, @step_blocks INT64,
--   @std_types ARRAY<STRING>, @post_first_step INT64, @post_sample INT64
-- The "snapshot_filter" placeholder in braces becomes an OR of block ranges, so BigQuery skips other blocks.
WITH steps AS (
  SELECT s.step, s.snap_start, s.snap_end FROM UNNEST(@steps) AS s
),
tx_addresses AS (
  SELECT
    block_number,
    ARRAY_CONCAT(
      ARRAY(SELECT i.addresses[OFFSET(0)] FROM UNNEST(inputs) AS i
            WHERE i.type IN UNNEST(@std_types) AND ARRAY_LENGTH(i.addresses) = 1),
      ARRAY(SELECT o.addresses[OFFSET(0)] FROM UNNEST(outputs) AS o
            WHERE o.type IN UNNEST(@std_types) AND ARRAY_LENGTH(o.addresses) = 1)
    ) AS addresses
  FROM `bigquery-public-data.crypto_bitcoin.transactions`
  WHERE block_timestamp_month BETWEEN @month_lo AND @month_hi
    AND ({snapshot_filter})
),
candidates AS (
  SELECT DISTINCT address, s.step, s.snap_end, FALSE AS extra
  FROM tx_addresses AS t, UNNEST(t.addresses) AS address
  JOIN steps AS s ON s.step = DIV(t.block_number - @base_block, @step_blocks) + 1
  WHERE t.block_number BETWEEN s.snap_start AND s.snap_end
  -- Labelled addresses observed at every step (rows appear only where they were active in the
  -- window): @extra_all at all steps (e.g. OFAC, ransomware), @extra_post at steps >= @post_first_step.
  UNION ALL
  SELECT address, s.step, s.snap_end, TRUE FROM UNNEST(@extra_all) AS address CROSS JOIN steps AS s
  UNION ALL
  SELECT address, s.step, s.snap_end, TRUE FROM UNNEST(@extra_post) AS address CROSS JOIN steps AS s
  WHERE s.step >= @post_first_step
),
active AS (
  SELECT address, step, ANY_VALUE(snap_end) AS snap_end, LOGICAL_OR(extra) AS extra
  FROM candidates
  GROUP BY address, step
)
SELECT address, step, snap_end
FROM active
WHERE TRUE
QUALIFY step < @post_first_step OR extra
  OR ROW_NUMBER() OVER (PARTITION BY step, extra ORDER BY FARM_FINGERPRINT(address), address) <= @post_sample
