-- Wallet window features for every (address, time step) in the active-address table.
-- This is app/features/wallet_window.py written in SQL; read that file for the rules.
-- training/check_sql_parity.py and check_live_parity.py compare the two on real addresses.
--
-- One pass over the public transactions table. Never reference `tx` (or anything built from
-- it) twice: BigQuery would scan the table again, without the partition filter, and bill it.
--
-- Parameters: @month_lo, @month_hi DATE; @block_lo, @block_hi INT64; @std_types ARRAY<STRING>;
--   @window_blocks INT64; @week_blocks INT64; @max_txs INT64; @round_sats INT64.
-- Placeholders (filled in by pull_bigquery.py): "active_table" in braces = result table of
--   active_addresses.sql; "stat_columns" in braces = the flattened statistic columns.
-- Besides the features it returns payer_fps and payee_fps: FARM_FINGERPRINT of every
-- counterparty that paid the wallet / was paid by it, for exposure-to-known-label features.
CREATE TEMP FUNCTION median_of(xs ARRAY<FLOAT64>) AS ((
  SELECT IF(n = 0, 0, IF(MOD(n, 2) = 1, s[OFFSET(DIV(n, 2))],
                         (s[OFFSET(DIV(n, 2) - 1)] + s[OFFSET(DIV(n, 2))]) / 2))
  FROM (SELECT ARRAY(SELECT x FROM UNNEST(xs) AS x ORDER BY x) AS s, IFNULL(ARRAY_LENGTH(xs), 0) AS n)
));
CREATE TEMP FUNCTION stats_of(xs ARRAY<FLOAT64>) AS (STRUCT(
  IFNULL((SELECT SUM(x) FROM UNNEST(xs) AS x), 0) AS total,
  IFNULL((SELECT MIN(x) FROM UNNEST(xs) AS x), 0) AS min,
  IFNULL((SELECT MAX(x) FROM UNNEST(xs) AS x), 0) AS max,
  IFNULL((SELECT AVG(x) FROM UNNEST(xs) AS x), 0) AS mean,
  median_of(xs) AS median
));
-- Block differences between consecutive transactions.
CREATE TEMP FUNCTION gaps_of(bs ARRAY<INT64>) AS (ARRAY(
  SELECT CAST(s[OFFSET(i)] - s[OFFSET(i - 1)] AS FLOAT64)
  FROM (SELECT ARRAY(SELECT b FROM UNNEST(bs) AS b ORDER BY b) AS s),
       UNNEST(GENERATE_ARRAY(1, IFNULL(ARRAY_LENGTH(bs), 0) - 1)) AS i
));
-- How many kept transactions each counterparty appears in.
CREATE TEMP FUNCTION counterparty_counts(fps ARRAY<INT64>) AS (ARRAY(
  SELECT CAST(COUNT(*) AS FLOAT64) FROM UNNEST(fps) AS f GROUP BY f
));
CREATE TEMP FUNCTION distinct_ids(fps ARRAY<INT64>) AS (ARRAY(
  SELECT DISTINCT f FROM UNNEST(fps) AS f ORDER BY f
));

WITH active_by_address AS (
  SELECT address, ARRAY_AGG(STRUCT(step, snap_end)) AS steps
  FROM {active_table}
  GROUP BY address
),
-- The only read of the public table: one row per transaction, with one part per standard
-- address saying what it sent (inputs) and received (outputs).
tx AS (
  SELECT
    `hash` AS txid,
    block_number,
    block_timestamp,
    IF(is_coinbase, 0, CAST(fee AS INT64)) AS fee,
    IF(is_coinbase, 0, CAST(input_value AS INT64)) AS input_value,
    virtual_size AS vsize,
    input_count,
    output_count,
    ARRAY(
      SELECT AS STRUCT address, SUM(sent) AS sent, SUM(received) AS received,
             LOGICAL_OR(side = 'in') AS is_sender, LOGICAL_OR(side = 'out') AS is_receiver
      FROM (
        SELECT i.addresses[OFFSET(0)] AS address, CAST(i.value AS INT64) AS sent, 0 AS received, 'in' AS side
        FROM UNNEST(inputs) AS i
        WHERE i.type IN UNNEST(@std_types) AND ARRAY_LENGTH(i.addresses) = 1
        UNION ALL
        SELECT o.addresses[OFFSET(0)], 0, CAST(o.value AS INT64), 'out'
        FROM UNNEST(outputs) AS o
        WHERE o.type IN UNNEST(@std_types) AND ARRAY_LENGTH(o.addresses) = 1
      )
      GROUP BY address
    ) AS parts
  FROM `bigquery-public-data.crypto_bitcoin.transactions`
  WHERE block_timestamp_month BETWEEN @month_lo AND @month_hi
    AND block_number BETWEEN @block_lo AND @block_hi
),
-- Mark each (transaction, address) with the time steps whose window contains it.
joined AS (
  SELECT
    t.txid, t.block_number, t.block_timestamp, t.fee, t.input_value, t.vsize, t.input_count, t.output_count,
    p.address, p.sent, p.received, p.is_sender, p.is_receiver,
    ARRAY(SELECT AS STRUCT s.step, s.snap_end FROM UNNEST(a.steps) AS s
          WHERE t.block_number BETWEEN s.snap_end - @window_blocks + 1 AND s.snap_end) AS steps
  FROM tx AS t, UNNEST(t.parts) AS p
  LEFT JOIN active_by_address AS a ON a.address = p.address
),
-- Back to one row per transaction, keeping all parts (for counterparties) and the wanted legs.
per_tx AS (
  SELECT
    txid, block_number,
    ANY_VALUE(block_timestamp) AS block_timestamp,
    ANY_VALUE(fee) AS fee,
    ANY_VALUE(input_value) AS input_value,
    ANY_VALUE(vsize) AS vsize,
    ANY_VALUE(input_count) AS input_count,
    ANY_VALUE(output_count) AS output_count,
    ARRAY_AGG(STRUCT(FARM_FINGERPRINT(address) AS fp, is_sender, is_receiver, received)) AS parts,
    ARRAY_AGG(IF(ARRAY_LENGTH(steps) > 0,
                 STRUCT(address, sent, received, is_sender, is_receiver, steps), NULL) IGNORE NULLS) AS wanted
  FROM joined
  GROUP BY txid, block_number
  HAVING ARRAY_LENGTH(wanted) > 0
),
legs AS (
  SELECT
    w.address, s.step, s.snap_end, t.txid, t.block_number, t.block_timestamp, t.fee, t.input_value,
    t.vsize, t.input_count, t.output_count,
    w.sent, w.received, w.is_sender, w.is_receiver,
    -- Payees: who it paid when sending. Payers: who paid it when receiving. Never itself.
    IF(w.is_sender, ARRAY(SELECT p.fp FROM UNNEST(t.parts) AS p
                          WHERE p.is_receiver AND p.fp != FARM_FINGERPRINT(w.address)), ARRAY<INT64>[]) AS payees,
    IF(w.is_receiver, ARRAY(SELECT p.fp FROM UNNEST(t.parts) AS p
                            WHERE p.is_sender AND p.fp != FARM_FINGERPRINT(w.address)), ARRAY<INT64>[]) AS payers,
    -- Sent someone else a round amount (per-address total of outputs, a multiple of @round_sats).
    w.is_sender AND EXISTS(SELECT 1 FROM UNNEST(t.parts) AS p
                           WHERE p.is_receiver AND p.fp != FARM_FINGERPRINT(w.address)
                             AND p.received > 0 AND MOD(p.received, @round_sats) = 0) AS round_payment
  FROM per_tx AS t, UNNEST(t.wanted) AS w, UNNEST(w.steps) AS s
),
anchored AS (
  SELECT *,
    ARRAY(SELECT DISTINCT f FROM UNNEST(ARRAY_CONCAT(payees, payers)) AS f) AS counterparties,
    MAX(block_number) OVER (PARTITION BY address, step) AS anchor_block
  FROM legs
),
ranked AS (
  SELECT *,
    COUNT(*) OVER (PARTITION BY address, step) AS n_in_window,
    ROW_NUMBER() OVER (PARTITION BY address, step ORDER BY block_number DESC, txid DESC) AS rn,
    block_number >= anchor_block - @week_blocks + 1 AS in_week
  FROM anchored
  WHERE block_number >= anchor_block - @window_blocks + 1
),
grouped AS (
  SELECT
    address, step,
    ANY_VALUE(snap_end) AS snap_end,
    ANY_VALUE(anchor_block) AS anchor_block,
    ANY_VALUE(n_in_window) AS n_in_window,
    COUNT(*) AS total_txs,
    COUNTIF(is_sender) AS num_txs_as_sender,
    COUNTIF(is_receiver) AS num_txs_as_receiver,
    MAX(block_number) - MIN(block_number) AS lifetime_in_blocks,
    MIN(IF(is_sender, block_number, NULL)) AS first_sent_block,
    MIN(IF(is_receiver, block_number, NULL)) AS first_received_block,
    COUNT(DISTINCT DATE(block_timestamp)) AS num_active_days,
    COUNTIF(is_receiver AND received > 0 AND MOD(received, @round_sats) = 0) AS n_round_received,
    COUNTIF(round_payment) AS n_round_payment,
    COUNTIF(is_sender AND is_receiver) AS n_self_change,
    COUNTIF(in_week) AS total_txs_1w,
    SUM(IF(in_week, received, 0)) / 1e8 AS btc_received_total_1w,
    SUM(IF(in_week, sent, 0)) / 1e8 AS btc_sent_total_1w,
    ARRAY_LENGTH(distinct_ids(ARRAY_CONCAT_AGG(IF(in_week, counterparties, ARRAY<INT64>[])))) AS num_counterparties_1w,
    ARRAY_AGG((sent + received) / 1e8) AS v_btc_transacted,
    ARRAY_AGG(IF(is_sender, sent / 1e8, NULL) IGNORE NULLS) AS v_btc_sent,
    ARRAY_AGG(IF(is_receiver, received / 1e8, NULL) IGNORE NULLS) AS v_btc_received,
    ARRAY_AGG(fee / 1e8) AS v_fees,
    ARRAY_AGG(IF(input_value > 0, fee / input_value, 0)) AS v_fees_as_share,
    ARRAY_AGG(IF(vsize > 0, fee / vsize, 0)) AS v_fee_rate,
    ARRAY_AGG(CAST(input_count AS FLOAT64)) AS v_tx_inputs,
    ARRAY_AGG(CAST(output_count AS FLOAT64)) AS v_tx_outputs,
    gaps_of(ARRAY_AGG(block_number)) AS v_blocks_btwn_txs,
    gaps_of(ARRAY_AGG(IF(is_sender, block_number, NULL) IGNORE NULLS)) AS v_blocks_btwn_input_txs,
    gaps_of(ARRAY_AGG(IF(is_receiver, block_number, NULL) IGNORE NULLS)) AS v_blocks_btwn_output_txs,
    counterparty_counts(ARRAY_CONCAT_AGG(counterparties)) AS v_transacted_w_address,
    distinct_ids(ARRAY_CONCAT_AGG(payers)) AS payer_fps,
    distinct_ids(ARRAY_CONCAT_AGG(payees)) AS payee_fps
  FROM ranked
  WHERE rn <= @max_txs
  GROUP BY address, step
),
with_stats AS (
  SELECT
    *,
    stats_of(v_btc_transacted) AS s_btc_transacted,
    stats_of(v_btc_sent) AS s_btc_sent,
    stats_of(v_btc_received) AS s_btc_received,
    stats_of(v_fees) AS s_fees,
    stats_of(v_fees_as_share) AS s_fees_as_share,
    stats_of(v_blocks_btwn_txs) AS s_blocks_btwn_txs,
    stats_of(v_blocks_btwn_input_txs) AS s_blocks_btwn_input_txs,
    stats_of(v_blocks_btwn_output_txs) AS s_blocks_btwn_output_txs,
    stats_of(v_transacted_w_address) AS s_transacted_w_address,
    stats_of(v_fee_rate) AS s_fee_rate,
    stats_of(v_tx_inputs) AS s_tx_inputs,
    stats_of(v_tx_outputs) AS s_tx_outputs
  FROM grouped
)
SELECT
  address, step, snap_end, anchor_block, n_in_window,
  total_txs, num_txs_as_sender, num_txs_as_receiver, lifetime_in_blocks,
  IFNULL(anchor_block - first_sent_block, -1) AS blocks_since_first_sent,
  IFNULL(anchor_block - first_received_block, -1) AS blocks_since_first_received,
  num_active_days,
  IFNULL(ARRAY_LENGTH(v_transacted_w_address), 0) AS num_counterparties,
  (SELECT COUNTIF(c > 1) FROM UNNEST(v_transacted_w_address) AS c) AS num_addr_transacted_multiple,
  IF(n_in_window > @max_txs, 1, 0) AS window_truncated,
  IF(num_txs_as_receiver > 0, n_round_received / num_txs_as_receiver, 0) AS round_received_share,
  IF(num_txs_as_sender > 0, n_round_payment / num_txs_as_sender, 0) AS round_payment_share,
  IF(num_txs_as_sender > 0, n_self_change / num_txs_as_sender, 0) AS self_change_share,
  total_txs_1w, btc_received_total_1w, btc_sent_total_1w, num_counterparties_1w,
  {stat_columns},
  payer_fps,
  payee_fps
FROM with_stats
