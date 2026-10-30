# Ransomware and random-address training check

Generated 2026-10-30 13:39 UTC by training/ransomware_check.py.
Train: steps 1-34. Ransomware positives: 11,256 Locky rows (5,248 addresses). Random addresses: 300,000 rows at weight 0.5.
Test: Elliptic++ steps 35-49 (97,754 rows); Locky addresses first active at step 17+ (3,934 rows); unseen families (1,082 rows, 63 families: Unlabeled, Ryuk, SamSam, DMALockerv3, HC6/HC7, CryptConsole, Conti, WannaCry...). Exposure features use only training-side
ransomware labels. Model: tuned XGBoost, v2 features + exposure.

## Elliptic++ test steps 35-49

| Model | PR-AUC | ROC-AUC | Threshold | Precision | Recall | F1 | Random flagged (35-49) | Random flagged (2017-19) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A  Elliptic++ only | 0.649 | 0.936 | best F1 (0.79) | 0.850 | 0.461 | 0.598 | 5.0% | 1.8% |
| A  Elliptic++ only | 0.649 | 0.936 | 1% random (0.98) | 0.954 | 0.302 | 0.459 | 0.8% | 0.1% |
| B  + Locky ransomware | 0.656 | 0.932 | best F1 (0.66) | 0.832 | 0.476 | 0.605 | 5.7% | 2.1% |
| B  + Locky ransomware | 0.656 | 0.932 | 1% random (0.97) | 0.957 | 0.287 | 0.441 | 0.7% | 0.1% |
| C  + Locky + random addresses | 0.591 | 0.927 | best F1 (0.29) | 0.781 | 0.417 | 0.544 | 1.5% | 1.2% |
| C  + Locky + random addresses | 0.591 | 0.927 | 1% random (0.62) | 0.920 | 0.262 | 0.408 | 0.4% | 0.3% |

## Ransomware the model never trained on

ROC-AUC: ransomware rows against random active addresses of the same steps. Caught: share of ransomware
rows above the threshold.

| Model | Test | Rows | ROC-AUC | Caught (best-F1 threshold) | Caught (1% threshold) |
| --- | --- | --- | --- | --- | --- |
| A  Elliptic++ only | Locky, addresses first active at step 17+ | 3,934 | 0.291 | 0.2% | 0.0% |
| A  Elliptic++ only | Unseen families (all other ransomware) | 1,082 | 0.532 | 3.9% | 0.2% |
| B  + Locky ransomware | Locky, addresses first active at step 17+ | 3,934 | 0.932 | 82.2% | 32.0% |
| B  + Locky ransomware | Unseen families (all other ransomware) | 1,082 | 0.574 | 8.4% | 0.1% |
| C  + Locky + random addresses | Locky, addresses first active at step 17+ | 3,934 | 0.976 | 85.1% | 74.8% |
| C  + Locky + random addresses | Unseen families (all other ransomware) | 1,082 | 0.622 | 4.3% | 1.3% |

## What this means

- Labels work for the family they come from. A model trained only on Elliptic++ ranked new Locky addresses
  below random ones (ROC-AUC 0.29); adding earlier Locky addresses lifts it to 0.93, and 0.98 with random
  addresses as presumed licit.
- Families the model never saw stay hard (ROC-AUC 0.53 to 0.62). Each new kind of crime needs some labels of
  its own, so keep adding labelled addresses and retrain.
- Random addresses as presumed licit cut false flags (1.5% of random 2016-17 addresses at the best-F1
  threshold, 0.4% at the strict one) for a small Elliptic++ loss (PR-AUC 0.649 to 0.591).
- The earlier "28.5% of random addresses flagged" came mostly from a low threshold. For live scoring, use the
  strict threshold (flags 1% of random addresses).
- Recommended live model: backend/models/xgboost_all_labels.joblib, trained on Elliptic++, every Ransomwhere
  family, and random addresses (training/train_models.py). Thresholds are in backend/models/model_card.json.
