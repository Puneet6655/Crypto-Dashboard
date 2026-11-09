# BigQuery + Elliptic++ training dataset

Generated 2026-10-30 09:38 UTC by training/build_training_dataset.py.
File: `data/processed/training_dataset.parquet` (gitignored; rebuild with the commands below).

## What a row is

One (address, time step). The address was active in that step's snapshot. Its features describe its
on-chain activity in the 4,032 blocks (about four weeks) ending at its newest transaction in the snapshot,
using at most its 100 newest transactions. `app/features/wallet_window.py` computes the same features
from live mempool.space data, so a model trained on this table can score live lookups.

## Time steps and dates

Elliptic++ has no dates, only time steps 1-49. Its wallet file has real block heights, and they show
that step t is a snapshot of 6-16 blocks (under three hours) starting at block 391,200 + 2016 x (t - 1).
2016 blocks is one difficulty period, about two weeks. Block times come from BigQuery.
Elliptic++ therefore covers 2016-01-01 02:50 to 2017-10-02 17:21 UTC.
Steps 50-101 continue the same schedule (9-block snapshots) for two years: 2017-10-15 16:07 to 2019-09-01 21:10 UTC.
The full table is in `data/processed/bigquery/time_steps.csv` and at the end of this report.

## Rows and labels

- Rows: 4,077,631 (bq_post: 1,452,417, bq_same_era: 1,704,528, elliptic_pp: 920,686)
- Elliptic++ wallet rows found in BigQuery: 920,686 of 920,691 (100.00%)
- Labels exist only on `elliptic_pp` rows: 1 illicit, 0 licit, empty for Elliptic++ class 3 (unknown).
  Unknown is not licit; do not use it as a negative.
- `external_label` (ransomware / sanctioned), `external_family`, `external_first_seen/_last_seen`: the
  address is on the Ransomwhere or OFAC list (data/raw/indicators/bitcoin_labels.csv). Not a training label
  by default; used to check models on data Elliptic++ never saw.
- `known_elliptic_class` repeats an address's Elliptic++ class on any row (including `bq_post`). It is
  for drift studies, not a label: an address can change owner or use over two years.
- `split`: train = steps 1-34, test = 35-49 (the Elliptic++ paper's split), post = 50-101. Some
  addresses appear in both train and test steps; split by address too when evaluating.

Labelled rows by split:

| Split | Licit (0) | Illicit (1) |
| --- | --- | --- |
| test | 92,731 | 5,023 |
| train | 183,966 | 9,697 |

## Features

Names follow Elliptic++ where the idea is the same, but the values are recomputed from all on-chain
transactions, not copied from the Elliptic++ CSV (which counts only transactions inside its sample).
Amounts are BTC; a statistic over an empty list is 0. Block heights are relative to the anchor, so
no feature encodes the era directly.

- Counts: total_txs, num_txs_as_sender, num_txs_as_receiver, num_active_days, window_truncated
- Timing: lifetime_in_blocks, blocks_since_first_sent / _received (-1 if none), blocks_btwn_txs_*,
  blocks_btwn_input_txs_*, blocks_btwn_output_txs_*
- Amounts: btc_transacted_*, btc_sent_*, btc_received_*, fees_*, fees_as_share_* (fee / all input value)
- Counterparties: num_counterparties, num_addr_transacted_multiple, transacted_w_address_* (transactions
  per counterparty; counterparties = who it paid when sending, who paid it when receiving)
- Network context (144 blocks ending at the anchor): net_tx_count_144, net_avg_fee_btc_144,
  fee_vs_network_mean (the wallet's mean fee over the network's). Live: mempool.space block list.
  The first two mostly identify the date, so a model can learn 'when' instead of 'what';
  training/baseline_model.py leaves them out by default (see reports/baseline_model.md).
- Transaction shape: fee_rate_* (sat/vB), tx_inputs_*, tx_outputs_*, round_received_share,
  round_payment_share (round = a multiple of 0.001 BTC), self_change_share
- Last week of the window: total_txs_1w, btc_received_total_1w, btc_sent_total_1w, num_counterparties_1w
- Address type from the address text: addr_p2pkh, addr_p2sh, addr_bech32
- Exposure (training/exposure.py): illicit_/licit_ payers and payees, as counts and shares of all payers
  or payees. Illicit = Elliptic++ class 1 + Ransomwhere; licit = Elliptic++ class 2. A label counts only
  if it was known before the row's step. OFAC has no dates, so it is used for live lookups only.
- Each `_*` family has _total, _min, _max, _mean, _median.

Not model features: address, step, period, split, source, labels, snap_end(_time), anchor_block/_time,
n_in_window (transactions in the window before the 100 cap).

## Checks

- `python -m training.check_sql_parity`: the SQL and the Python function agree on every value
  (359 wallets x 56 values on step 25, including capped and counterparty-heavy wallets).
- 77 transactions from block 500,000 covering every script type came out identical from BigQuery and
  mempool.space after conversion.
- `python -m training.check_live_parity`: recomputes sample rows from live mempool.space data. On
  2026-10-30, 25 of 25 rows (labelled, same-era and post) matched, including 3 network-context checks.
- `daily_network_stats.csv`: the first and last day are partial (see its `blocks` column).

## Known differences from live data

- Live scoring anchors at the address's newest transaction now; training anchors at the snapshot.
  An address with no transactions returns all-zero features: do not score it.
- When more than 100 transactions share the window edge, BigQuery and mempool.space may keep a
  different transaction from the same block. Only busy wallets (window_truncated = 1) are affected.
- Era: 2015-2019 behaviour, fee levels and address types differ from today (see
  training/FEATURE_MISMATCH.md). Use the bq_post rows to measure drift before trusting scores.

## BigQuery cost

On-demand pricing bills bytes scanned; the first 1 TB per month is free. Every query was dry-run first
and sent with a byte cap. Billed so far by these scripts:

| Query | Estimated GB | Billed GB |
| --- | --- | --- |
| month_bounds | 0.00 | 0.01 |
| month_bounds | 0.00 | 0.01 |
| month_bounds | 0.00 | 0.01 |
| parity_active | 0.01 | 0.01 |
| parity_features | 0.29 | 0.28 |
| parity_raw_txs | 0.28 | 0.28 |
| tx_parity_raw | 0.03 | 0.03 |
| month_bounds | 0.00 | 0.01 |
| block_stats | 14.55 | 14.55 |
| active_addresses | 2.18 | 2.17 |
| wallet_features | 157.42 | 157.40 |
| month_bounds | 0.00 | 0.01 |
| month_bounds | 0.00 | 0.01 |
| parity_active | 0.03 | 0.01 |
| parity_features | 0.33 | 0.29 |
| parity_raw_txs | 0.33 | 0.29 |
| month_bounds | 0.00 | 0.01 |
| parity_active | 0.03 | 0.01 |
| parity_features | 0.33 | 0.29 |
| parity_raw_txs | 0.33 | 0.29 |
| month_bounds | 0.00 | 0.01 |
| month_bounds | 0.00 | 0.01 |
| active_addresses_v2 | 2.19 | 2.17 |
| wallet_features_v2 | 166.18 | 166.15 |
| fingerprints | 0.00 | 0.00 |
| fingerprints | 0.00 | 0.00 |
| fingerprints | 0.00 | 0.00 |
| **Total** | | **344.3** |

## Rebuild

```
cd backend
..\.venv\Scripts\python.exe -m pip install -r requirements-ml.txt
..\.venv\Scripts\python.exe -m training.pull_bigquery plan
..\.venv\Scripts\python.exe -m training.pull_bigquery run
..\.venv\Scripts\python.exe -m training.build_training_dataset
```

## Time step table

| Step | Period | Blocks | Start (UTC) | End (UTC) |
| --- | --- | --- | --- | --- |
| 1 | elliptic | 391,200-391,212 | 2016-01-01 02:50 | 2016-01-01 05:56 |
| 2 | elliptic | 393,216-393,224 | 2016-01-13 23:40 | 2016-01-14 01:33 |
| 3 | elliptic | 395,232-395,242 | 2016-01-27 04:01 | 2016-01-27 05:27 |
| 4 | elliptic | 397,248-397,258 | 2016-02-07 17:04 | 2016-02-07 18:40 |
| 5 | elliptic | 399,264-399,275 | 2016-02-20 05:41 | 2016-02-20 07:31 |
| 6 | elliptic | 401,280-401,287 | 2016-03-05 14:13 | 2016-03-05 15:45 |
| 7 | elliptic | 403,296-403,310 | 2016-03-19 03:05 | 2016-03-19 05:18 |
| 8 | elliptic | 405,312-405,324 | 2016-04-01 21:14 | 2016-04-01 23:26 |
| 9 | elliptic | 407,328-407,342 | 2016-04-14 22:53 | 2016-04-15 01:04 |
| 10 | elliptic | 409,344-409,353 | 2016-04-29 01:55 | 2016-04-29 03:25 |
| 11 | elliptic | 411,360-411,367 | 2016-05-11 23:10 | 2016-05-12 00:34 |
| 12 | elliptic | 413,376-413,382 | 2016-05-25 15:34 | 2016-05-25 16:19 |
| 13 | elliptic | 415,392-415,401 | 2016-06-08 18:08 | 2016-06-08 19:24 |
| 14 | elliptic | 417,408-417,416 | 2016-06-21 22:21 | 2016-06-21 23:02 |
| 15 | elliptic | 419,424-419,433 | 2016-07-05 17:19 | 2016-07-05 18:52 |
| 16 | elliptic | 421,440-421,447 | 2016-07-19 17:27 | 2016-07-19 18:36 |
| 17 | elliptic | 423,456-423,470 | 2016-08-03 10:01 | 2016-08-03 11:23 |
| 18 | elliptic | 425,472-425,478 | 2016-08-16 13:52 | 2016-08-16 14:42 |
| 19 | elliptic | 427,488-427,496 | 2016-08-30 06:26 | 2016-08-30 07:55 |
| 20 | elliptic | 429,504-429,515 | 2016-09-12 19:15 | 2016-09-12 20:46 |
| 21 | elliptic | 431,520-431,530 | 2016-09-25 21:50 | 2016-09-25 23:59 |
| 22 | elliptic | 433,536-433,547 | 2016-10-09 00:36 | 2016-10-09 02:24 |
| 23 | elliptic | 435,552-435,564 | 2016-10-23 10:58 | 2016-10-23 13:00 |
| 24 | elliptic | 437,568-437,583 | 2016-11-06 07:34 | 2016-11-06 09:22 |
| 25 | elliptic | 439,584-439,591 | 2016-11-18 23:59 | 2016-11-19 01:12 |
| 26 | elliptic | 441,600-441,607 | 2016-12-02 19:42 | 2016-12-02 20:38 |
| 27 | elliptic | 443,616-443,621 | 2016-12-15 20:26 | 2016-12-15 20:56 |
| 28 | elliptic | 445,632-445,638 | 2016-12-29 09:37 | 2016-12-29 10:31 |
| 29 | elliptic | 447,648-447,655 | 2017-01-11 12:49 | 2017-01-11 14:08 |
| 30 | elliptic | 449,664-449,671 | 2017-01-23 15:14 | 2017-01-23 16:14 |
| 31 | elliptic | 451,680-451,688 | 2017-02-05 13:26 | 2017-02-05 14:46 |
| 32 | elliptic | 453,696-453,706 | 2017-02-19 01:06 | 2017-02-19 02:11 |
| 33 | elliptic | 455,712-455,719 | 2017-03-04 11:05 | 2017-03-04 12:00 |
| 34 | elliptic | 457,728-457,734 | 2017-03-18 00:21 | 2017-03-18 01:15 |
| 35 | elliptic | 459,744-459,753 | 2017-03-31 07:38 | 2017-03-31 10:34 |
| 36 | elliptic | 461,760-461,770 | 2017-04-13 20:45 | 2017-04-13 23:05 |
| 37 | elliptic | 463,776-463,782 | 2017-04-27 20:05 | 2017-04-27 20:57 |
| 38 | elliptic | 465,792-465,800 | 2017-05-10 20:00 | 2017-05-10 20:46 |
| 39 | elliptic | 467,808-467,814 | 2017-05-23 22:00 | 2017-05-23 22:52 |
| 40 | elliptic | 469,824-469,837 | 2017-06-05 05:37 | 2017-06-05 06:55 |
| 41 | elliptic | 471,840-471,846 | 2017-06-18 16:13 | 2017-06-18 18:03 |
| 42 | elliptic | 473,856-473,868 | 2017-07-02 13:09 | 2017-07-02 15:18 |
| 43 | elliptic | 475,872-475,880 | 2017-07-15 03:48 | 2017-07-15 05:28 |
| 44 | elliptic | 477,888-477,897 | 2017-07-28 01:01 | 2017-07-28 03:14 |
| 45 | elliptic | 479,904-479,911 | 2017-08-10 05:38 | 2017-08-10 08:14 |
| 46 | elliptic | 481,920-481,927 | 2017-08-25 00:54 | 2017-08-25 01:42 |
| 47 | elliptic | 483,936-483,944 | 2017-09-07 03:20 | 2017-09-07 05:13 |
| 48 | elliptic | 485,952-485,960 | 2017-09-19 00:54 | 2017-09-19 01:44 |
| 49 | elliptic | 487,968-487,975 | 2017-10-02 16:58 | 2017-10-02 17:21 |
| 50 | post | 489,984-489,992 | 2017-10-15 16:07 | 2017-10-15 17:02 |
| 51 | post | 492,000-492,008 | 2017-10-27 16:10 | 2017-10-27 17:52 |
| 52 | post | 494,016-494,024 | 2017-11-11 15:01 | 2017-11-11 17:04 |
| 53 | post | 496,032-496,040 | 2017-11-25 11:13 | 2017-11-25 12:56 |
| 54 | post | 498,048-498,056 | 2017-12-07 07:38 | 2017-12-07 08:48 |
| 55 | post | 500,064-500,072 | 2017-12-19 02:42 | 2017-12-19 05:25 |
| 56 | post | 502,080-502,088 | 2018-01-01 18:36 | 2018-01-01 19:22 |
| 57 | post | 504,096-504,104 | 2018-01-13 23:25 | 2018-01-14 00:30 |
| 58 | post | 506,112-506,120 | 2018-01-25 23:28 | 2018-01-26 00:53 |
| 59 | post | 508,128-508,136 | 2018-02-07 18:09 | 2018-02-07 19:01 |
| 60 | post | 510,144-510,152 | 2018-02-20 23:46 | 2018-02-21 00:29 |
| 61 | post | 512,160-512,168 | 2018-03-05 20:59 | 2018-03-05 21:52 |
| 62 | post | 514,176-514,184 | 2018-03-19 01:54 | 2018-03-19 04:08 |
| 63 | post | 516,192-516,200 | 2018-04-01 21:47 | 2018-04-01 23:33 |
| 64 | post | 518,208-518,216 | 2018-04-14 17:49 | 2018-04-14 19:02 |
| 65 | post | 520,224-520,232 | 2018-04-28 04:27 | 2018-04-28 06:32 |
| 66 | post | 522,240-522,248 | 2018-05-11 21:07 | 2018-05-11 22:10 |
| 67 | post | 524,256-524,264 | 2018-05-25 03:03 | 2018-05-25 04:02 |
| 68 | post | 526,272-526,280 | 2018-06-06 11:24 | 2018-06-06 11:49 |
| 69 | post | 528,288-528,296 | 2018-06-20 00:24 | 2018-06-20 01:37 |
| 70 | post | 530,304-530,312 | 2018-07-03 11:41 | 2018-07-03 13:26 |
| 71 | post | 532,320-532,328 | 2018-07-17 17:07 | 2018-07-17 18:32 |
| 72 | post | 534,336-534,344 | 2018-07-30 00:57 | 2018-07-30 03:17 |
| 73 | post | 536,352-536,360 | 2018-08-12 00:01 | 2018-08-12 01:01 |
| 74 | post | 538,368-538,376 | 2018-08-25 07:53 | 2018-08-25 09:16 |
| 75 | post | 540,384-540,392 | 2018-09-07 19:20 | 2018-09-07 20:03 |
| 76 | post | 542,400-542,408 | 2018-09-21 15:06 | 2018-09-21 15:47 |
| 77 | post | 544,416-544,424 | 2018-10-04 22:59 | 2018-10-04 23:56 |
| 78 | post | 546,432-546,440 | 2018-10-19 14:04 | 2018-10-19 16:28 |
| 79 | post | 548,448-548,456 | 2018-11-02 14:34 | 2018-11-02 15:42 |
| 80 | post | 550,464-550,472 | 2018-11-17 16:20 | 2018-11-17 17:25 |
| 81 | post | 552,480-552,488 | 2018-12-04 07:09 | 2018-12-04 10:01 |
| 82 | post | 554,496-554,504 | 2018-12-19 14:12 | 2018-12-19 15:29 |
| 83 | post | 556,512-556,520 | 2019-01-01 10:16 | 2019-01-01 11:08 |
| 84 | post | 558,528-558,536 | 2019-01-14 17:40 | 2019-01-14 18:35 |
| 85 | post | 560,544-560,552 | 2019-01-28 23:29 | 2019-01-29 01:11 |
| 86 | post | 562,560-562,568 | 2019-02-11 08:48 | 2019-02-11 09:24 |
| 87 | post | 564,576-564,584 | 2019-02-25 09:41 | 2019-02-25 10:32 |
| 88 | post | 566,592-566,600 | 2019-03-11 11:33 | 2019-03-11 12:52 |
| 89 | post | 568,608-568,616 | 2019-03-24 15:05 | 2019-03-24 16:53 |
| 90 | post | 570,624-570,632 | 2019-04-07 17:45 | 2019-04-07 18:34 |
| 91 | post | 572,640-572,648 | 2019-04-21 18:51 | 2019-04-21 19:33 |
| 92 | post | 574,656-574,664 | 2019-05-05 04:03 | 2019-05-05 04:51 |
| 93 | post | 576,672-576,680 | 2019-05-18 23:39 | 2019-05-19 00:29 |
| 94 | post | 578,688-578,696 | 2019-05-31 18:15 | 2019-05-31 19:50 |
| 95 | post | 580,704-580,712 | 2019-06-14 15:33 | 2019-06-14 16:47 |
| 96 | post | 582,720-582,728 | 2019-06-27 19:20 | 2019-06-27 20:41 |
| 97 | post | 584,736-584,744 | 2019-07-10 01:56 | 2019-07-10 03:37 |
| 98 | post | 586,752-586,760 | 2019-07-24 02:20 | 2019-07-24 04:10 |
| 99 | post | 588,768-588,776 | 2019-08-05 18:23 | 2019-08-05 19:08 |
| 100 | post | 590,784-590,792 | 2019-08-19 09:55 | 2019-08-19 11:20 |
| 101 | post | 592,800-592,808 | 2019-09-01 19:32 | 2019-09-01 21:10 |
