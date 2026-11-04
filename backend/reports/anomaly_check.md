# Anomaly check: Isolation Forest on v2+exp features

Generated 2026-11-04T10:41:40+00:00 by training/anomaly_check.py. Same data and split as the model registry: Elliptic++ labelled wallets, train steps 1-34 (193,663 rows), test steps 35-49 (97,754 rows, 5,023 illicit, base rate 5.1%). Features: v2+exp (89, signed_log for the forest). Forest: 300 trees, 1024 samples per tree. Three seeds; mean ± sd.

| Scorer | Test PR-AUC |
| --- | --- |
| Anomaly score alone (forest fit on all train rows, labels unused) | 0.038 ± 0.001 |
| Anomaly score alone (forest fit on licit train rows only) | 0.041 ± 0.001 |
| Tuned XGBoost alone (v2+exp) | 0.664 ± 0.002 |
| XGBoost + anomaly score as a feature | 0.665 ± 0.002 |
| Rank blend 90% XGBoost / 10% anomaly | 0.604 ± 0.003 |
| Rank blend 75% / 25% | 0.413 ± 0.020 |
| Rank blend 50% / 50% | 0.175 ± 0.026 |
| XGBoost ranks only (blend reference: ranking changes PR-AUC ties slightly) | 0.664 ± 0.002 |

Top 1% most anomalous test rows (seed 0): 978 rows, 10 illicit and 968 licit (1.0% illicit against a 5.1% base rate). With the licit-only forest: 10 illicit, 968 licit.

## What this means

- The anomaly score alone is a weak illicit detector (PR-AUC 0.038 against a 0.051 base rate, XGBoost 0.664): unusual is not the same as illicit.
- It even points the wrong way: ROC-AUC 0.38 (below 0.5), and the top 1% most unusual rows are mostly licit: in these features Elliptic++ illicit wallets look more typical than licit ones.
- As an extra XGBoost feature it changes PR-AUC by +0.001; the best rank blend (weight 0.1) changes it by -0.060. A change smaller than about twice the seed spread (0.004) is noise.
- Use it as a separate review list ("unusual, not flagged"), not as part of the alert score.

## Rebuild

```
cd backend
..\.venv\Scripts\python.exe -m training.anomaly_check offline
..\.venv\Scripts\python.exe -m training.anomaly_check recent
```
