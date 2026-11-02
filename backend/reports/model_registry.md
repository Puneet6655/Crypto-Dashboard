# Model registry and offline benchmark

Generated 2026-11-02T09:05:40+00:00 by training/model_registry.py. Offline benchmark on Elliptic++ wallets: train steps 1-34, threshold on steps 31-34, test steps 35-49 (about Jun 2017 to Sep 2017).
Test rows: 97,754 (5,023 illicit).

## Comparison (seed 0 model; threshold chosen on validation steps 31-34)

| Model | Features | PR-AUC | ROC-AUC | Precision | Recall | F1 | PR-AUC mean ± sd (seeds) | Beats baseline |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Logistic regression (baseline) | v2+exp | 0.381 | 0.853 | 0.454 | 0.442 | 0.448 | 0.381 ± 0.000 (1) | baseline |
| Random Forest (tuned) | v2+exp | 0.537 | 0.865 | 0.474 | 0.542 | 0.506 | 0.530 ± 0.005 (3) | yes |
| XGBoost (tuned) | v2+exp | 0.666 | 0.939 | 0.845 | 0.465 | 0.600 | 0.664 ± 0.002 (3) | yes |
| Logistic regression (baseline) | v2+exp+hops | 0.465 | 0.892 | 0.383 | 0.567 | 0.457 | 0.465 ± 0.000 (1) | baseline |
| Random Forest (tuned) | v2+exp+hops | 0.629 | 0.934 | 0.590 | 0.594 | 0.592 | 0.629 ± 0.003 (3) | yes |
| XGBoost (tuned) | v2+exp+hops | 0.756 | 0.961 | 0.874 | 0.525 | 0.656 | 0.750 ± 0.005 (3) | yes |

Confusion matrices at each model's threshold (test steps 35-49):

| Model | Features | Threshold | TP | FP | FN | TN |
| --- | --- | --- | --- | --- | --- | --- |
| Logistic regression (baseline) | v2+exp | 0.96 | 2,218 | 2,664 | 2,805 | 90,067 |
| Random Forest (tuned) | v2+exp | 0.26 | 2,725 | 3,018 | 2,298 | 89,713 |
| XGBoost (tuned) | v2+exp | 0.77 | 2,333 | 427 | 2,690 | 92,304 |
| Logistic regression (baseline) | v2+exp+hops | 0.88 | 2,850 | 4,593 | 2,173 | 88,138 |
| Random Forest (tuned) | v2+exp+hops | 0.27 | 2,985 | 2,079 | 2,038 | 90,652 |
| XGBoost (tuned) | v2+exp+hops | 0.76 | 2,638 | 379 | 2,385 | 92,352 |

## Alert threshold re-picked on recent data (steps 50-101)

Model: XGBoost v2+exp+hops trained on all 291,417 labelled rows of steps 1-49. There are no
Elliptic++ labels after step 49, so the threshold is the score that flags 1% of random active addresses
(presumed mostly licit), picked on steps 50-75 and checked on steps 76-101.

| Threshold | Value | Random flagged, steps 1-49 | Steps 50-75 | Steps 76-101 | Ransomware rows flagged, steps 50-101 |
| --- | --- | --- | --- | --- | --- |
| Old (steps 1-49) | 0.9918 | 1.00% | 0.10% | 0.05% | 0.7% |
| Recent (steps 50-75) | 0.9142 | 3.68% | 1.00% | 0.76% | 3.2% |

Ransomware rows after step 49: 562.

## Rebuild

```
cd backend
..\.venv\Scripts\python.exe -m training.model_registry
```
