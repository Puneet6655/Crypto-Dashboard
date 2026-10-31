# Multi-hop graph features

Generated 2026-10-31 14:43 UTC by training/hop_check.py (features: training/hop_features.py,
transactions: training/transaction_store.py). Model: tuned XGBoost (reports/tuning.md). Split as in
reports/improvement_check.md: train steps 1-34 (threshold from steps 31-34), test steps 35-49 (97,754 labelled rows).

## Results

### 1. Trained once on steps 1-34 (best-F1 threshold)

| Features | PR-AUC | ROC-AUC | Precision | Recall | F1 |
| --- | --- | --- | --- | --- | --- |
| v2+exp | 0.663 | 0.939 | 0.822 | 0.479 | 0.605 |
| + hops 1-2 | 0.735 | 0.955 | 0.832 | 0.537 | 0.653 |
| + hops 1-5 | 0.761 | 0.961 | 0.876 | 0.528 | 0.659 |

### 2. Retrained before each test step (precision/recall/F1 at the trained-once threshold)

| Features | PR-AUC | ROC-AUC | Precision | Recall | F1 |
| --- | --- | --- | --- | --- | --- |
| v2+exp | 0.753 | 0.958 | 0.874 | 0.552 | 0.677 |
| + hops 1-2 | 0.810 | 0.968 | 0.876 | 0.640 | 0.739 |
| + hops 1-5 | 0.833 | 0.972 | 0.918 | 0.635 | 0.750 |

### 3. Address-disjoint test (94,197 test rows whose address has no labelled row in steps 1-34)

| Features | PR-AUC | ROC-AUC | Precision | Recall | F1 |
| --- | --- | --- | --- | --- | --- |
| v2+exp | 0.665 | 0.938 | 0.822 | 0.484 | 0.609 |
| + hops 1-2 | 0.737 | 0.955 | 0.831 | 0.541 | 0.655 |
| + hops 1-5 | 0.760 | 0.961 | 0.875 | 0.528 | 0.659 |

### 4. Strict threshold (flags 1% of random active addresses in steps 31-34)

| Features | Threshold | Precision | Recall | F1 | Random flagged, steps 35-49 | Random flagged at best-F1 threshold |
| --- | --- | --- | --- | --- | --- | --- |
| v2+exp | 0.988 | 0.960 | 0.276 | 0.429 | 0.47% | 5.36% |
| + hops 1-2 | 0.992 | 0.980 | 0.288 | 0.446 | 0.40% | 8.03% |
| + hops 1-5 | 0.993 | 0.974 | 0.282 | 0.438 | 0.44% | 6.61% |

### 5. Share of rows at each hop distance from the nearest known-illicit address (6 = none within 5)

| Rows | 1 | 2 | 3 | 4 | 5 | none |
| --- | --- | --- | --- | --- | --- | --- |
| Illicit (test steps 35-49) | 19.1% | 23.3% | 25.3% | 20.4% | 9.4% | 2.5% |
| Licit (test steps 35-49) | 1.7% | 27.0% | 33.5% | 19.9% | 4.1% | 13.8% |
| Random active addresses (steps 35-49) | 0.7% | 8.3% | 19.9% | 23.2% | 12.7% | 35.2% |
| All rows, steps 50-101 | 0.4% | 7.7% | 16.5% | 23.5% | 12.5% | 39.3% |

Same for the nearest known-licit address:

| Rows | 1 | 2 | 3 | 4 | 5 | none |
| --- | --- | --- | --- | --- | --- | --- |
| Illicit (test steps 35-49) | 9.5% | 39.4% | 41.0% | 6.9% | 1.4% | 1.7% |
| Licit (test steps 35-49) | 56.5% | 28.0% | 11.1% | 2.1% | 0.8% | 1.5% |
| Random active addresses (steps 35-49) | 13.7% | 25.5% | 21.2% | 10.6% | 5.6% | 23.4% |
| All rows, steps 50-101 | 13.5% | 22.3% | 19.8% | 11.1% | 5.5% | 27.8% |

### Hop features by importance (share of total gain, + hops 1-5 model)

| Feature | Gain share |
| --- | --- |
| illicit_btc_share_1hop | 13.3% |
| hop2_mean_log_counterparties | 10.4% |
| licit_within_2 | 4.0% |
| illicit_hop_dist | 1.5% |
| licit_hop_dist | 1.4% |
| licit_btc_share_1hop | 1.3% |
| illicit_within_2 | 1.1% |
| hop2_mean_log_total_txs | 1.1% |
| licit_decayed_5 | 0.6% |
| hop2_mean_log_btc_received | 0.3% |
| illicit_decayed_5 | 0.3% |
| licit_within_1 | 0.1% |
| illicit_within_1 | 0.1% |

All hop features together: 35.6% of the model's gain.

## What this means

- Hop features help clearly. Trained once, PR-AUC rises from 0.663 (v2+exp) to 0.735 with hops 1-2 and 0.761
  with hops 1-5; retrained before each step, from 0.753 to 0.810 and 0.833. F1 rises from 0.605 to 0.659
  (0.677 to 0.750 retrained). The gain holds on the address-disjoint test (0.665 to 0.760), so it is not the
  model remembering addresses it saw in training.
- Hops 3-5 add a smaller but real gain on top of hops 1-2: +0.026 PR-AUC trained once, +0.023 retrained, and
  higher precision at the same threshold. Most of the lift comes from 1-2 hops.
- The strongest hop features are the BTC share exchanged directly with known-illicit addresses and the 2-hop
  neighbourhood size (how busy the counterparties' counterparties are). Hop distance itself matters less
  than how much value moved and how dense the neighbourhood is.
- Hop distance separates the classes: 42% of illicit test rows are within 2 hops of an earlier illicit label,
  against 29% of licit rows and 9% of random active addresses; 57% of licit rows are 1 hop from a known
  licit address, against 10% of illicit rows.
- At the strict threshold (1% of random addresses in steps 31-34) the model flags 0.4% of random addresses in
  steps 35-49 with 97-98% precision, but recall stays near 28-29%: hop features make the top of the list
  cleaner without catching many more illicit rows there. At the best-F1 threshold they flag more random
  addresses (5.4% to 6.6-8.0%), so use the strict threshold for live alerts.
- Caveats: Elliptic++ itself was sampled around labelled transactions, so part of the gain may reflect how
  that sample was drawn; random-address checks are the best guard. The graph only contains transactions of
  addresses active in each snapshot (and their 100 newest transactions), so live lookups need the same
  neighbourhood (each counterparty's newest transactions) to reproduce these features. Ransomware families
  and OFAC were not re-tested here.

## Alert queue data

Built by training/alert_queue.py with the + hops 1-5 model trained on all Elliptic++ labels (steps 1-49); strict threshold 0.9916 (flags 1% of random active addresses in steps 1-49).

- data/processed/alert_queue/addresses.parquet: 46,566 rows (one per address and step), 17,412 distinct addresses, 4.4 MB.
  - known_illicit: 37,677 rows (20,875 Elliptic++, 16,802 Ransomwhere): labelled illicit before the step and active in it.
  - near_illicit: 8,889 rows within 2 hops of a known-illicit address with score >= threshold (3,845 at 1 hop, 5,044 at 2 hops; 8,586 in steps 1-49, 303 in steps 50-101).
    2,571 of them are Elliptic++ illicit rows and 3 licit; the rest are unlabelled.
  - Columns: alert_id, address, step, snap_end_time, anchor_time, anchor_block, reason, score, threshold,
    above_threshold, hop_distance (0 for known), nearest_seed, nearest_seed_source, evidence_tx_hashes (path
    from the address to the seed), illicit_within_1/_2, illicit_btc_share_1hop, licit_hop_dist, elliptic_label,
    external_family, score_in_training (true for labelled steps 1-49 rows the model was fit on), model_features.
- data/processed/alert_queue/transactions.parquet: 410,572 transactions (493 MB): every saved transaction of the step that
  touches an alert address, plus every evidence transaction. Columns: step, tx_hash, block_height, block_time,
  is_coinbase, fee_sats, total_input_btc, total_output_btc, inputs / outputs (list of {address, value} in sats),
  alert_addresses, is_evidence.
- Few near_illicit rows after step 49: the threshold comes from 2016-17 addresses and scores drift lower
  in 2018-19. Re-pick the threshold on recent random addresses before live use.

## How the features are built

- Transactions: for each training row (address, step), the 100 newest transactions touching the address in
  the 4,032 blocks ending at the step's snapshot, the same transactions the wallet features use. Saved with
  every input and output (address, value) under data/raw/bigquery/transactions/step=NNN/.
- Graph per step: an address is linked to another when one paid the other in a saved transaction (each
  input address to each output address; co-spending inputs are not linked). It contains nothing after the
  step's snapshot. Addresses that are not training rows appear only through the transactions of rows, so
  paths longer than one hop run through other addresses active in that step's snapshot.
- Seeds: illicit = Elliptic++ class 1 from an earlier step + Ransomwhere addresses first seen before the
  step; licit = Elliptic++ class 2 from an earlier step. Labels of the row's own step or later are never
  used, and a row's own label never counts toward its features. OFAC is not used (no dates).
- Hubs: addresses with more than 1,000 counterparties in the step's graph. A path can end at
  a hub but never passes through one; seeds still reach their direct counterparties.
- Multi-source BFS from the seeds, up to 5 hops. illicit_hop_dist: 1-5, 6 = none (hops 1-2 set: 1, 2,
  3 = none within 2). illicit_within_1/_2: distinct seeds within 1 / 2 hops (exact).
  illicit_btc_share_1hop: share of the row's BTC in its own transactions that came from or went to seeds
  in the same transaction, split by value. illicit_decayed_2/_5: sum over the row's counterparties of
  0.5^hops to the nearest seed through that counterparty. Same for licit_.
- hop2_mean_log_*: mean of log1p(transactions / BTC received / counterparties) over 2-hop walks from the
  row (not through hubs), using graph versions of those values computed from the saved transactions.
- Approximation: for a row that is itself a seed, the nearest other seed is found through counterparties
  whose nearest seed is a different address; a counterparty equally close to the row and to another seed
  may be missed. Counts within 1-2 hops are exact.

## Graph size

| | Min | Median | Max |
| --- | --- | --- | --- |
| transactions | 103,100 | 203,940 | 568,579 |
| addresses | 1,096,987 | 1,637,028 | 3,168,759 |
| links | 6,468,604 | 14,084,517 | 108,348,455 |
| hubs | 654 | 1,475 | 11,428 |
| rows | 25,765 | 32,862 | 70,063 |
| rows that are hubs | 78 | 168 | 453 |
| illicit seeds | 3 | 460 | 2,791 |
| illicit seed hubs | 0 | 0 | 3 |
| licit seeds | 0 | 19,839 | 32,368 |
| seconds total | 28 | 94 | 469 |

Hubs over all steps: 234,289 (address, step) pairs, 19,219 of them training rows; 59 illicit seeds were hubs.

## Data and cost

- Transaction store: 24,398,884 (step, transaction) rows, 16.70 GB of Parquet.
- BigQuery billed for this work: 188.0 GB (11 transaction queries + a 0.44 GB copy of the
  active-address table). Each query was dry-run first and sent with a byte cap; see
  data/processed/bigquery/cost_log.csv.

## Rebuild

```
cd backend
..\.venv\Scripts\python.exe -m training.transaction_store plan
..\.venv\Scripts\python.exe -m training.transaction_store run
..\.venv\Scripts\python.exe -m training.hop_features
..\.venv\Scripts\python.exe -m training.hop_check
..\.venv\Scripts\python.exe -m training.alert_queue
```
