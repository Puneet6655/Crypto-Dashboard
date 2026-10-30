# Model comparison and drift fixes

Generated 2026-10-30 04:16 UTC by training/compare_models.py.
Train: Elliptic++ labelled rows, steps 1-34 (193,663 rows, 9,697 illicit). Test: steps 35-49 (97,754 rows, 5,023 illicit). Illicit is the positive class;
the decision threshold is picked on the last four training steps. Features: the live-compatible set
(wallet features + fee_vs_network_mean). Default settings, no tuning.

## 1. XGBoost, Random Forest and the baseline (train once)

| Model | Features | PR-AUC | ROC-AUC | Precision | Recall | F1 |
| --- | --- | --- | --- | --- | --- | --- |
| Baseline (HistGB) | raw | 0.284 | 0.839 | 0.481 | 0.180 | 0.262 |
| Baseline (HistGB) | ranked within step | 0.271 | 0.811 | 0.289 | 0.267 | 0.277 |
| XGBoost | raw | 0.353 | 0.862 | 0.464 | 0.295 | 0.361 |
| XGBoost | ranked within step | 0.274 | 0.804 | 0.264 | 0.266 | 0.265 |
| Random Forest | raw | 0.299 | 0.801 | 0.558 | 0.205 | 0.300 |
| Random Forest | ranked within step | 0.305 | 0.810 | 0.351 | 0.276 | 0.309 |

"ranked within step" = each feature replaced by its percentile among all active addresses in the same
time step (Fix 1). Live, the reference would be a recent sample of active addresses.

## 2. Data drift: can a model tell 2016-17 rows from 2017-19 rows?

- Raw features: ROC-AUC 0.965
- Ranked within step: ROC-AUC 1.000

0.5 means the two periods look the same to a model; 1.0 means they are always told apart.
Ranking leaks the time step: tied values (for example every 0 in a yes/no column) get a percentile that is
different in every step, so a model can read the step from them. This fix, as built, does not work.

## 3. Known Elliptic++ addresses seen again in 2017-19

6,086 rows (288 with an illicit Elliptic++ class). A weak check: the class is from
2016-17 and an address may have changed use, but it is the only labelled signal after 2017.

| Model | Features | ROC-AUC | PR-AUC |
| --- | --- | --- | --- |
| Baseline (HistGB) | raw | 0.814 | 0.140 |
| Baseline (HistGB) | ranked within step | 0.726 | 0.089 |
| XGBoost | raw | 0.818 | 0.151 |
| XGBoost | ranked within step | 0.755 | 0.099 |
| Random Forest | raw | 0.584 | 0.058 |
| Random Forest | ranked within step | 0.522 | 0.051 |

## 4. Retraining as labels arrive (XGBoost, raw features)

Static: trained once on steps 1-34. Retrained: before each test step, trained on every earlier step.

- All test steps, PR-AUC: static 0.353, retrained 0.500

| Step | Illicit | F1 static | F1 retrained | PR-AUC static | PR-AUC retrained |
| --- | --- | --- | --- | --- | --- |
| 35 | 442 | 0.769 | 0.769 | 0.731 | 0.731 |
| 36 | 597 | 0.210 | 0.323 | 0.450 | 0.525 |
| 37 | 440 | 0.665 | 0.818 | 0.734 | 0.922 |
| 38 | 194 | 0.447 | 0.505 | 0.495 | 0.529 |
| 39 | 129 | 0.323 | 0.386 | 0.318 | 0.334 |
| 40 | 453 | 0.318 | 0.414 | 0.326 | 0.401 |
| 41 | 182 | 0.404 | 0.594 | 0.414 | 0.749 |
| 42 | 411 | 0.485 | 0.500 | 0.441 | 0.633 |
| 43 | 105 | 0.101 | 0.177 | 0.072 | 0.106 |
| 44 | 284 | 0.532 | 0.362 | 0.604 | 0.488 |
| 45 | 33 | 0.038 | 0.022 | 0.014 | 0.020 |
| 46 | 509 | 0.000 | 0.041 | 0.131 | 0.118 |
| 47 | 204 | 0.021 | 0.056 | 0.076 | 0.125 |
| 48 | 388 | 0.021 | 0.241 | 0.191 | 0.438 |
| 49 | 652 | 0.269 | 0.639 | 0.421 | 0.743 |

## What solves the drift problem

1. Retrain whenever new labels arrive. Section 4 shows it: PR-AUC on steps 35-49 rises from 0.353 to 0.500,
   and step 49 F1 from 0.27 to 0.64. It cannot fix steps 45-47 at once: there the illicit behaviour was new,
   and no earlier label shows it.
2. Get recent labels. This is the only real fix for scoring today's addresses. Public sources: OFAC sanctioned
   Bitcoin addresses, ransomware payment lists (for example Ransomwhere), plus outcomes of this dashboard's own
   investigations. BigQuery computes the same features for any address at any date cheaply (a few GB per
   snapshot thanks to the block filter), so new labels can join the training table directly.
3. Watch drift on live lookups: run the 2016-17 vs now classifier on a batch of live feature rows, and show
   scores as experimental while it can tell them apart. Re-pick the threshold on recent labelled data.
4. Not a fix: ranking features within each step (Section 2). Untested ideas: amounts in USD instead of BTC,
   address-type features.

Saved models (training/train_models.py): backend/models/xgboost.joblib and random_forest.joblib, trained
on all labelled steps 1-49, with thresholds in backend/models/model_card.json.
