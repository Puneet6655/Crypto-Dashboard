-- Every transaction the training rows use, with all inputs and outputs, for a range of time steps.
-- A training row is (address, step); it uses the @max_txs newest transactions touching the address
-- in the @window_blocks blocks ending at the step's snapshot end (the same rule as
-- wallet_window_features.sql, so these are exactly the transactions behind the features).
-- One output row per (step, transaction). A transaction used by rows of two steps appears twice.
--
-- One read of the public table (never reference `tx` twice: BigQuery would scan it again).
-- Parameters: @month_lo, @month_hi DATE; @block_lo, @block_hi INT64; @step_lo, @step_hi INT64;
--   @std_types ARRAY<STRING>; @window_blocks INT64; @max_txs INT64.
-- Placeholder "active_table" in braces: result table of active_addresses.sql (address, step, snap_end).
-- address is NULL for inputs/outputs without exactly one standard address (P2PK, multisig, OP_RETURN).
WITH active_by_address AS (
  SELECT address, ARRAY_AGG(STRUCT(step, snap_end)) AS steps
  FROM {active_table}
  WHERE step BETWEEN @step_lo AND @step_hi
  GROUP BY address
),
tx AS (
  SELECT
    `hash` AS tx_hash,
    block_number,
    block_timestamp,
    is_coinbase,
    IF(is_coinbase, 0, CAST(fee AS INT64)) AS fee,
    ARRAY(
      SELECT AS STRUCT
        IF(i.type IN UNNEST(@std_types) AND ARRAY_LENGTH(i.addresses) = 1, i.addresses[OFFSET(0)], NULL) AS address,
        CAST(i.value AS INT64) AS value
      FROM UNNEST(inputs) AS i ORDER BY i.index
    ) AS inputs,
    ARRAY(
      SELECT AS STRUCT
        IF(o.type IN UNNEST(@std_types) AND ARRAY_LENGTH(o.addresses) = 1, o.addresses[OFFSET(0)], NULL) AS address,
        CAST(o.value AS INT64) AS value
      FROM UNNEST(outputs) AS o ORDER BY o.index
    ) AS outputs
  FROM `bigquery-public-data.crypto_bitcoin.transactions`
  WHERE block_timestamp_month BETWEEN @month_lo AND @month_hi
    AND block_number BETWEEN @block_lo AND @block_hi
),
legs AS (
  SELECT t.tx_hash, t.block_number, t.block_timestamp, t.is_coinbase, t.fee, t.inputs, t.outputs,
         a.address AS row_address, s.step
  FROM tx AS t
  CROSS JOIN UNNEST(ARRAY(
    SELECT DISTINCT x.address FROM UNNEST(ARRAY_CONCAT(t.inputs, t.outputs)) AS x WHERE x.address IS NOT NULL
  )) AS tx_address
  JOIN active_by_address AS a ON a.address = tx_address
  CROSS JOIN UNNEST(a.steps) AS s
  WHERE t.block_number BETWEEN s.snap_end - @window_blocks + 1 AND s.snap_end
  QUALIFY ROW_NUMBER() OVER (PARTITION BY a.address, s.step ORDER BY t.block_number DESC, t.tx_hash DESC) <= @max_txs
)
SELECT
  step,
  tx_hash,
  ANY_VALUE(block_number) AS block_height,
  ANY_VALUE(block_timestamp) AS block_time,
  ANY_VALUE(is_coinbase) AS is_coinbase,
  ANY_VALUE(fee) AS fee_sats,
  ANY_VALUE(inputs) AS inputs,
  ANY_VALUE(outputs) AS outputs,
  ARRAY_AGG(row_address ORDER BY row_address) AS row_addresses
FROM legs
GROUP BY step, tx_hash
