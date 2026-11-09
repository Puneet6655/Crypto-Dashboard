-- Every transaction in a block range, with all inputs and outputs, for recent scoring
-- (training/score_recent.py). Same input/output rules as window_transactions.sql, plus the
-- virtual size the fee-rate features need.
-- One read of the public table. Parameters: @month_lo, @month_hi DATE; @block_lo, @block_hi INT64;
--   @std_types ARRAY<STRING>.
-- address is NULL for inputs/outputs without exactly one standard address (P2PK, multisig, OP_RETURN).
SELECT
  `hash` AS tx_hash,
  block_number AS block_height,
  block_timestamp AS block_time,
  is_coinbase,
  IF(is_coinbase, 0, CAST(fee AS INT64)) AS fee_sats,
  CAST(virtual_size AS INT64) AS vsize,
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
