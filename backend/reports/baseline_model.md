# Baseline model check

Generated 2026-10-30 03:41 UTC by training/baseline_model.py. Not a tuned model.

Model: scikit-learn HistGradientBoostingClassifier, balanced class weights, trained on Elliptic++ labelled
rows from steps 1-34 (193,663 rows, 9,697 illicit). Decision threshold 0.88, picked on steps 31-34. Illicit is the positive class; Elliptic++ 'unknown' rows are excluded.

## Illicit detection on later steps

| Evaluation | Rows (illicit) | PR-AUC | ROC-AUC | Precision | Recall | F1 |
| --- | --- | --- | --- | --- | --- | --- |
| test steps 35-49 | 97,754 (5,023) | 0.284 | 0.839 | 0.481 | 0.180 | 0.262 |
| test, addresses unseen in training | 94,197 (4,947) | 0.284 | 0.837 | 0.480 | 0.181 | 0.263 |
| same, plus raw network columns | 97,754 (5,023) | 0.320 | 0.853 | 0.434 | 0.283 | 0.342 |
| reference: Elliptic++ CSV features | 97,754 (5,023) | 0.385 | 0.887 | 0.323 | 0.584 | 0.416 |

The first rows use the live-compatible features from training_dataset.parquet: the wallet features plus
fee_vs_network_mean. net_tx_count_144 and net_avg_fee_btc_144 are left out by default because they mostly
say *when* a row is from (see Drift), which does not carry over to new periods. The reference row uses
the Elliptic++ CSV's own wallet features on the same split; those cannot be computed from live data.

## By test step

| Step | Illicit | Licit | F1 |
| --- | --- | --- | --- |
| 35 | 442 | 4973 | 0.570 |
| 36 | 597 | 8540 | 0.094 |
| 37 | 440 | 2980 | 0.150 |
| 38 | 194 | 3356 | 0.519 |
| 39 | 129 | 3687 | 0.349 |
| 40 | 453 | 8203 | 0.108 |
| 41 | 182 | 5688 | 0.451 |
| 42 | 411 | 9114 | 0.492 |
| 43 | 105 | 6127 | 0.049 |
| 44 | 284 | 7939 | 0.558 |
| 45 | 33 | 9279 | 0.029 |
| 46 | 509 | 4086 | 0.000 |
| 47 | 204 | 6843 | 0.000 |
| 48 | 388 | 8103 | 0.005 |
| 49 | 652 | 3813 | 0.222 |

## Drift after 2017

- A model telling 2016-17 rows from 2017-19 rows reaches ROC-AUC 0.962 with the default features and 1.000 with the raw network columns added (0.5 = no difference, 1.0 = always tells them apart).
- Share of rows flagged illicit: 1.9% on test steps 35-49, 1.8% on 2017-19 rows.
- 6,086 post-2017 rows belong to addresses with an Elliptic++ class (288 illicit). The model's ROC-AUC against that old class is 0.814 (a weak check: the class is from 2016-17).
