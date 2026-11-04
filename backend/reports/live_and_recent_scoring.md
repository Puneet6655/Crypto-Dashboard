# Live and recent (2026) scoring of the Bitcoin models

Written 4 Nov 2026. Covers live scoring in investigations, batch scoring of recent BigQuery data, the
re-picked 2026 alert threshold and the anomaly test. There are no 2026 labels, so nothing here measures
accuracy on 2026 data: it shows what the models flag, how often, and whether the features are built
the same way as in training.

## 1. Live scoring in investigations

The "Random Forest" and "XGBoost" investigation methods now score any mainnet Bitcoin address, not
only addresses in the 2017 benchmark rows.

- Features: `app/features/wallet_window.py`, the same functions as the training rows: the newest 100
  confirmed transactions in the 4,032 blocks (about four weeks) ending at the address's newest
  transaction, address type, network fee context (144 blocks before that) and exposure to known-label
  counterparties.
- Known labels (`app/ml/known_labels.py`, built into `models/known_labels.parquet` by
  `python -m training.known_labels`): illicit = Elliptic++ class 1 (14,266) + Ransomwhere (11,186) +
  OFAC XBT (532); licit = Elliptic++ class 2 (251,084). Training used only labels known before each
  step. Live scoring comes after all of them, so all count.
- Models: the live-compatible v2+exp entries in `models/registry.json` (`xgboost__v2_exp`,
  `random_forest__v2_exp`), columns in `entry["features"]` order, SHAP from `app/ml/explain.py`. The
  hop models are not used live: hop features need the counterparties' own histories.
- Result: `data_mode` "live". Findings give the score against the model's best-F1 threshold and the
  2026 alert threshold, the top SHAP contributions, the window that was scored and the known-label
  counterparties. Evidence: the provider's transaction pages (mempool.space, or the backup),
  the label lists, the model registry and SHAP. If the address also has a 2017 dataset row, its label
  and that row's score are added as an extra finding. If the backup provider (no block summaries)
  answered, the fee context is set to 0 and a limitation says so.
- Code: `app/ml/live_scoring.py`, `app/ml/scoring.py:investigate_with_model`, provider passed from
  `app/services/investigation_service.py`. `POST /api/models/score` (the Models page) is unchanged:
  it still scores the offline dataset rows.
- Tests: `tests/test_live_scoring.py` (fake mempool.space, synthetic models with the real 89 feature
  names). The full backend suite passes (134 tests).

Live smoke check on 4 Nov (mempool.space, real models). The first call takes about 35 s while the
models, SHAP and label lists load. Later calls take 4-26 s, mostly the 11 provider requests (1-4
transaction pages plus 10 block-summary pages):

| Address | Kind | XGBoost | Random Forest | Newest tx |
| --- | --- | --- | --- | --- |
| `34xp4vRoCGJym3xR7yCVPFHoCNxv4Twseo` | large exchange wallet | 0.000 | 0.045 | 25 Oct 2026 |
| `123WBUDmSJv4GctdVEz6Qq6z8nXSKrJ4KX` | OFAC-listed | 0.982 (flagged) | 0.116 | 18 Feb 2020 |
| `112dDMZmaymAW52AciQroksJmK7CLr7XJb` | Elliptic++ illicit, step 35 | 0.999 (flagged) | 0.913 (flagged) | 31 Mar 2017 |
| 5 OFAC/Ransomwhere addresses active in Oct-Nov 2026 | listed | all ≤ 0.007 | all ≤ 0.11 | 3-27 Oct 2026 |

The Elliptic++ address scored live gets exactly its dataset-row score (0.998751), so live and
BigQuery features agree for it. For dormant addresses the "last four weeks" are the four weeks
before their last transaction, which can be years ago.

## 2. Batch scoring of recent BigQuery data

- Store: `training/recent_store.py` keeps every transaction of the newest 5,040 blocks (the 4,032-block
  window plus one week, so an address whose newest transaction is a week old still has its full
  window). It reused the 2 Nov pull (6 days), added a one-column vsize backfill for it (0.37 GB, needed
  for the fee-rate features), and pulled only the missing blocks. A rerun pulls only blocks newer than
  the newest saved one. Store now: blocks 969,478-974,518 (1 Oct to 4 Nov 2026, tip at 10:54 UTC).
- Billed this task: **10.1 GB** (blocks 9.43 GB in 9 queries, vsize 0.37 GB, small tip/month queries).
  Downloading through the free REST API took about 13 minutes per 500 blocks (2.3M transactions).
- Scored (`training/score_recent.py`): 3,504,910 addresses were active in the newest week (blocks
  973,511-974,518). A random sample of 100,000 (seed 0) was scored, plus every active address on a
  known list (3 scam-list addresses, 192 Elliptic++-labelled ones), kept apart from the sample. 100,000
  rather than more because the hop graph must fit in 16 GB: this one has 297,023 transactions,
  3.4M addresses and 52M links, built in 103 s. The whole run takes about 4.5 minutes.
- Same rules as training: wallet window and exposure by the same Python functions; fee context counted
  from the store (it holds every transaction); `window_truncated` from the selection (0.21% of rows).
  Hop features use `training.hop_features.StepGraph` on the graph of all kept transactions, with
  every known label as a seed. Only 1 illicit and 200 licit seeds are in the graph at all, so every
  random row is "no illicit seed within 5 hops": the 2017 seeds are too far away to help.
- Parity: `training/check_recent_parity.py` recomputed 8 random small wallets live from mempool.space:
  8/8 identical on all 89 v2+exp features.
- Output (gitignored): `data/processed/recent_scores/` features.parquet, scores.parquet (scores of the
  three models, old/new flags, top-5 SHAP for flagged rows, nearest illicit seed), summary.json,
  parity.json.

## 3. The 2026 alert threshold

Rule as before (`alert_threshold.recent_threshold`): the score that flags 1% of random active
addresses. The random sample was split by address hash: picked on half A (49,961), checked on half B.

| Model | Old threshold | Random 2026 flagged at old | New 2026 threshold | Flagged at new (all / half B) |
| --- | --- | --- | --- | --- |
| XGBoost alert model (v2+exp+hops) | 0.9142 (steps 50-75) | 633 (0.63%) | **0.8538** | 982 (0.98% / 0.96%) |
| same, steps 1-49 threshold | 0.9918 | 76 (0.08%) | | |
| XGBoost v2+exp (live model) | 0.77 (best F1, 2017) | 809 (0.81%) | 0.7175 | 980 (0.98% / 0.96%) |
| Random Forest v2+exp | 0.26 (best F1, 2017) | 304 (0.30%) | 0.1967 | 942 (0.94% / 0.88%) |

Stored in `models/registry.json` (gitignored) as `alert_threshold.recent_2026_threshold` and
`recent_2026_threshold` on the two v2+exp entries, with the rule, tip block and date. Existing keys are
unchanged. Live investigations show the new value next to the model's own threshold.

Known-listed addresses caught: **none, and the test is too small to mean anything**. Only 3
listed addresses (all from the CryptoScamDB list, none OFAC or Ransomwhere) were active in the newest
week, and only 8 were active in the whole five weeks. None of the 3 is flagged at any threshold, and none
of the 5 OFAC/Ransomwhere addresses active in the five weeks is flagged live (table above). The one
active Elliptic++ illicit address scores 0.16 (live model) / 0.02 (alert model).

What gets flagged: 602 random addresses are flagged by both XGBoost models. The alert model's top
SHAP feature is mostly `hop2_mean_log_counterparties` (665 of 982) or `tx_outputs_min` (310). The live
model's is mostly `tx_inputs_median` (692 of 980). Flagged rows are typically short-lived wallets in
consolidation-like transactions (median 63 inputs per transaction, against 3 for unflagged ones).
These are probably sweeps and batching, not crime, but there is no 2026 label to say either way.

## 4. Anomaly test

Full numbers in [anomaly_check.md](anomaly_check.md). On Elliptic++ test steps 35-49, an Isolation
Forest's anomaly score has PR-AUC 0.038 (base rate 0.051, XGBoost 0.664) and ROC-AUC 0.38: illicit
wallets look *more* typical than licit ones. Only 10 of the 978 most anomalous test rows are illicit.
As an XGBoost feature it changes PR-AUC by +0.001 (noise). Rank blending only hurts (0.604 at 10% weight).
On the 2026 batch, none of the 1,000 most unusual random addresses is flagged by the alert model
(rank correlation -0.11). They are mostly wallets moving hundreds to thousands of BTC through a few
counterparties. The list is saved as `data/processed/recent_scores/unusual_not_flagged.parquet`:
leads for review, not alerts.

## 5. Caveats

- Labels are from 2016-2017 (plus Ransomwhere, mostly 2016, and OFAC). A 2026 score is a pattern
  match against 2017 behaviour. Address types, fees and services have all changed since: 83% of
  recent random addresses are bech32, which hardly existed in the training years.
- No 2026 labels, so no 2026 precision or recall. The 1% threshold only fixes the alert volume.
- The hop features carry almost no information on recent data, because almost no known seed is active.
  Recent labels (fresh OFAC additions, current ransomware and scam addresses) are what would make
  them useful.
- The random sample is of addresses active in one week. Training's random rows were addresses
  active in a 6-16 block snapshot, so the populations differ slightly.
- Live features cover only the last ~4 weeks of an address's activity (the newest 100 transactions).

## Rerun

```
cd backend
..\.venv\Scripts\python.exe -m training.known_labels
..\.venv\Scripts\python.exe -m training.recent_store plan      # dry runs: GB per missing range
..\.venv\Scripts\python.exe -m training.recent_store pull      # only new blocks; refuses above --max-total-gb
..\.venv\Scripts\python.exe -m training.score_recent           # also updates registry.json
..\.venv\Scripts\python.exe -m training.check_recent_parity
..\.venv\Scripts\python.exe -m training.anomaly_check recent
```
