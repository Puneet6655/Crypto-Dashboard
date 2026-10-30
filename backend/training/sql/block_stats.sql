-- Per-block network totals: the user's daily query, kept per block so the same numbers can
-- be rebuilt from mempool.space live (its block list gives tx_count and total fees).
-- tx_count includes the coinbase transaction; fees exclude it (BigQuery stores 0 for it).
-- Daily totals (date, total_transactions, total_fees_btc, avg_fee_btc) are summed from these rows.
-- Parameters: @month_lo, @month_hi DATE; @block_lo, @block_hi INT64.
SELECT
  block_number,
  MIN(block_timestamp) AS block_timestamp,
  COUNT(*) AS tx_count,
  SUM(IF(is_coinbase, 0, CAST(fee AS INT64))) AS total_fees_sats
FROM `bigquery-public-data.crypto_bitcoin.transactions`
WHERE block_timestamp_month BETWEEN @month_lo AND @month_hi
  AND block_number BETWEEN @block_lo AND @block_hi
GROUP BY block_number
