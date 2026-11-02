# ML pipeline and how to retrain

The models answer one narrow question: does a Bitcoin wallet's activity in a time window look like
the wallets Elliptic++ labelled illicit in 2016-2017? Every score is an **offline benchmark**. The
dashboard says so wherever a score appears, and scores work only for addresses in the benchmark
dataset, because live lookups cannot build the hop features yet (see "Known gaps" below).

All commands run from `backend/` with the root virtual environment:
`..\.venv\Scripts\python.exe -m training.<module>`. Install `requirements-ml.txt` first.
Outputs go to `data/processed/` and `models/` (both gitignored). Each step writes a report in
`reports/`.

## 1. The pipeline

```
Elliptic++ CSVs (data/raw/elliptic_pp/)                     inspect_elliptic -> reports/elliptic_pp_data_card.md
        │ addresses + time steps (step t = block 391,200 + 2016 x (t-1))
        ▼
BigQuery crypto_bitcoin ── pull_bigquery (plan / run) ──► wallet_window_features.parquet
        │                                                    (same feature code as live lookups:
        │                                                     app/features/wallet_window.py)
        ▼
build_training_dataset ──► data/processed/training_dataset.parquet
        │   4,077,631 rows (address, step): Elliptic++ wallets steps 1-49, random active
        │   addresses of the same era, and steps 50-101 (Oct 2017 to Sep 2019)
        │   + exposure features (training/exposure.py: labels known before the step)
        ▼
transaction_store (plan / run) ──► data/raw/bigquery/transactions/step=NNN/  (16.7 GB, 24.4M rows)
        ▼
hop_features ──► data/processed/hop_features/step=NNN.parquet
        │   per-step address graph, multi-source BFS from known illicit / licit seeds (labels
        │   from earlier steps only), hubs (>1,000 counterparties) not passed through
        ▼
model_registry ──► models/*.joblib, models/registry.json, models/scoring_rows.parquet
                    reports/model_registry.md
```

Checks along the way: `check_sql_parity` and `check_live_parity` (SQL features equal Python
features equal live mempool.space features), `baseline_model`, `compare_models`, `tune_models`,
`improvement_check`, `ransomware_check`, `hop_check`, `alert_queue`, `elliptic2` (a separate
subgraph benchmark). Their reports are in `reports/`.

## 2. Feature sets

- **v2+exp** (89 features, "live-compatible"): wallet window features (counts, BTC sent/received,
  fees, fee rate, timing, counterparties, inputs/outputs), transaction shape (round amounts,
  self-change), a one-week window, address type, fee versus the network mean, and exposure to
  known-illicit / known-licit counterparties. `app/features/wallet_window.py` can compute all of
  these from a live lookup.
- **v2+exp+hops** (102 features, "dataset only"): plus hop features: distance to the nearest known
  illicit / licit address (up to 5 hops), seeds within 1-2 hops, BTC share exchanged directly with
  seeds, decayed exposure, and 2-hop neighbourhood size. These need every counterparty's own recent
  transactions, which the live lookup does not fetch.

## 3. The benchmark (training/model_registry.py)

One fixed split for every model, following the plan's M10-M11 rules:

- Train: Elliptic++ labelled wallets of steps 1-34 (193,663 rows).
- Threshold: each model's best illicit F1 on steps 31-34, using a model fit on steps 1-30.
- Test: steps 35-49 (97,754 rows, 5,023 illicit; 31 Mar to 2 Oct 2017), used once.
- Seeds: Random Forest and XGBoost are trained with seeds 0, 1 and 2. A model "beats the baseline"
  only if its mean PR-AUC is above the logistic regression on the same features by more than twice
  its seed spread. The seed-0 model is saved and served.

Results (2 Nov 2026, `reports/model_registry.md`):

| Model | Features | PR-AUC | ROC-AUC | Precision | Recall | F1 | Beats baseline |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Logistic regression | v2+exp | 0.381 | 0.853 | 0.454 | 0.442 | 0.448 | baseline |
| Random Forest | v2+exp | 0.537 | 0.865 | 0.474 | 0.542 | 0.506 | yes |
| XGBoost | v2+exp | 0.666 | 0.939 | 0.845 | 0.465 | 0.600 | yes |
| Logistic regression | v2+exp+hops | 0.465 | 0.892 | 0.383 | 0.567 | 0.457 | baseline |
| Random Forest | v2+exp+hops | 0.629 | 0.934 | 0.590 | 0.594 | 0.592 | yes |
| XGBoost | v2+exp+hops | 0.756 | 0.961 | 0.874 | 0.525 | 0.656 | yes |

XGBoost is best on both feature sets; hop features add about 0.09 PR-AUC to every model. Retraining
before each test step lifts XGBoost further (0.753 without hops, 0.833 with hops 1-5:
`reports/hop_features.md`), so regular retraining matters more than model choice.

## 4. Alert threshold on recent data (M9)

There are no Elliptic++ labels after step 49. The alert threshold is the score that flags 1% of
random active addresses (presumed mostly licit), from the XGBoost v2+exp+hops model trained on every
label of steps 1-49 (`models/xgboost__alert_hops.joblib`):

| Threshold | Value | Random flagged 1-49 | 50-75 | 76-101 | Ransomware caught, steps 50+ |
| --- | --- | --- | --- | --- | --- |
| Old, picked on steps 1-49 | 0.9918 | 1.00% | 0.10% | 0.05% | 0.7% |
| Recent, picked on steps 50-75 | 0.9142 | 3.68% | 1.00% | 0.76% | 3.2% |

Scores drift down after 2017, so the old threshold flags almost nothing in 2018-19. The recent
threshold holds at about 1% on the unseen steps 76-101. Either way the model catches very few of the
562 Ransomwhere rows after step 49: it was trained on Elliptic++ illicit behaviour, which is not
ransomware behaviour (`reports/ransomware_check.md`). `training/alert_queue.py` still uses its own
steps 1-49 threshold; pass the recent one before using its output for 2018-19.

## 5. Serving (app/ml)

- `registry.py` reads `models/registry.json` (id, family, feature set, version = date + file hash,
  feature list, threshold and rule, test metrics, confusion matrix, seed spread, training rows) and
  loads models on first use.
- `scoring.py` scores one address from `models/scoring_rows.parquet` (99,963 rows: the labelled test
  rows of steps 35-49 plus Ransomwhere/OFAC-listed rows from step 35 on). By default it uses the
  address's latest step.
- `explain.py` gives SHAP values: TreeExplainer for Random Forest (probability units) and XGBoost
  (log-odds); exact closed form for the linear baseline (log-odds). Base value + contributions equals
  the model output; `tests/test_models.py` checks this for all three model types.
- API: `GET /api/models`, `GET /api/models/examples`, `POST /api/models/score`
  `{address, model_id?, step?}`. Investigation methods `random_forest` and `xgboost` use the
  v2+exp+hops model of that family and fill "Why Flagged?".

## 6. Retraining

Models only (data already built, about 15 minutes with a GPU):

```powershell
cd backend
..\.venv\Scripts\python.exe -m training.model_registry
```

Restart the API afterwards (models are cached in memory). The Models page reads the new
`registry.json`.

Everything, from scratch (needs the Elliptic++ CSVs, the Google key and about 360 GB of the BigQuery
free tier, so spread it over two months or check the month's usage first):

```powershell
..\.venv\Scripts\python.exe -m training.inspect_elliptic
..\.venv\Scripts\python.exe -m training.pull_bigquery plan      # prints GB per query; then:
..\.venv\Scripts\python.exe -m training.pull_bigquery run
..\.venv\Scripts\python.exe -m training.build_training_dataset
..\.venv\Scripts\python.exe -m training.transaction_store plan
..\.venv\Scripts\python.exe -m training.transaction_store run   # ~35 min per 10 steps to download
..\.venv\Scripts\python.exe -m training.hop_features            # run alone: big 2018 steps need most of 16 GB RAM
..\.venv\Scripts\python.exe -m training.model_registry
```

Practical notes:
- XGBoost trains on the GPU when one is usable (`training/xgb_device.py`, `XGB_DEVICE=cpu` to
  force CPU). GPU and CPU models differ very slightly; saved models are switched to CPU for serving.
- Python 3.15's joblib thread pool sometimes fails with "concurrent send_bytes"; the fit helpers
  retry (`tune_models.fit_retry`).
- The older `training/train_models.py` and `models/model_card.json` (30 Oct, models fit on all of
  steps 1-49 without hop features) are kept for the earlier reports; the dashboard uses the registry.

## 7. Known gaps

- Live addresses cannot be scored: hop features need each counterparty's recent transactions, which
  the live lookup does not fetch. Building them live (bounded 1-2 hop fetches through the failover
  provider) is the next step if live scoring is wanted.
- Labels end in October 2017. Recent behaviour (Taproot, current exchanges and mixers) is not
  represented; steps 50-101 are unlabelled apart from Ransomwhere and OFAC.
- Elliptic++ was sampled around labelled transactions; part of the gains may reflect how the sample
  was drawn. Random-address checks are the guard used throughout.
- Ransomware is poorly detected (see section 4). `models/xgboost_all_labels.joblib` (trained with
  Ransomwhere rows and random addresses) does better on ransomware but is not in the registry yet.
- SMOTE was not run (see `MODEL_DOSSIERS.md`); class weights are used instead.
