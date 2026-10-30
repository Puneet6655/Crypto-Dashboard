# Do the new features help?

Generated 2026-10-30 09:59 UTC by training/improvement_check.py.
Models use the tuned settings from reports/tuning.md. Feature sets: v1 = original window features;
v2 = + transaction shape, round amounts, 1-week window, address type; v2+exp = + exposure to known
illicit/licit counterparties (only labels known before the row's step).

## 1. Train on steps 1-34 once, test on steps 35-49

| Model | Features | PR-AUC | ROC-AUC | Precision | Recall | F1 |
| --- | --- | --- | --- | --- | --- | --- |
| XGBoost (tuned) | v1 | 0.446 | 0.869 | 0.514 | 0.435 | 0.471 |
| XGBoost (tuned) | v2 | 0.587 | 0.913 | 0.775 | 0.442 | 0.563 |
| XGBoost (tuned) | v2+exp | 0.663 | 0.939 | 0.822 | 0.479 | 0.605 |
| Random Forest (tuned) | v1 | 0.299 | 0.795 | 0.479 | 0.227 | 0.308 |
| Random Forest (tuned) | v2 | 0.488 | 0.849 | 0.733 | 0.439 | 0.550 |
| Random Forest (tuned) | v2+exp | 0.537 | 0.864 | 0.474 | 0.543 | 0.506 |

## 2. Retraining before each test step (XGBoost (tuned), v2+exp)

PR-AUC on steps 35-49: 0.753 (trained once: 0.663).

## 3. Labels Elliptic++ never used (XGBoost (tuned), v2+exp, trained on steps 1-49)

Known-bad rows scored against random active addresses of the same period. Random addresses are mostly
but not certainly licit, so these numbers are a lower bound. 'Flagged' uses the model's threshold (0.19).

| Check | Rows | ROC-AUC | Flagged | Random flagged |
| --- | --- | --- | --- | --- |
| Ransomwhere addresses, 2016-17 (not in Elliptic++) | 16,121 | 0.640 | 43.9% | 28.5% |
| Ransomwhere addresses, 2017-19 | 538 | 0.546 | 16.5% | 12.5% |
| Elliptic++ illicit addresses seen again in 2017-19 | 19,747 | 0.732 | 40.4% | 12.5% |

Elliptic++ illicit vs licit addresses seen again in 2017-19 (156,088 rows): ROC-AUC 0.854.

## What this means

- New transaction features and exposure to known labels are the big gains: tuned XGBoost goes from PR-AUC
  0.446 (v1) to 0.663 (v2+exp) when trained once, and 0.753 when retrained before each step. Steps 46-49,
  where the original model failed, reach PR-AUC 0.639 (training/train_models.py holdout).
- Exposure uses only labels known before each row's step. In the test that includes labels from earlier
  test steps, which matches a deployment where new labels keep arriving.
- Caveat 1, threshold: at 0.19 the model flags 28.5% of random 2016-17 addresses and 12.5% of 2017-19 ones.
  Elliptic++ is not a random sample of addresses, so live use needs a stricter threshold, or training with
  random active addresses as presumed-licit rows.
- Caveat 2, ransomware: Elliptic++ illicit behaviour is not ransomware behaviour (ROC-AUC 0.64 in 2016-17,
  0.55 in 2017-19). Next step: add the Ransomwhere rows as training positives and test on held-out families.
