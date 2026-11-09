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

## Recent batch (2026)

The saved forest (seed 0) scored 100,191 recent addresses (100,000 random). 1,000 random rows are in the top 1% most unusual (anomaly >= 0.528); 0 of them are also flagged by the alert model at the new threshold 0.8538, so 1,000 are "unusual but not flagged" (saved to data/processed/recent_scores/unusual_not_flagged.parquet). Rank correlation of anomaly and alert score on random rows: -0.11. Known-listed recent addresses in the top 1%: 1 of 3.

Most unusual random addresses the alert model does not flag (first 15):

| Address | Anomaly | Alert score | Txs in window | Max outputs | BTC received |
| --- | --- | --- | --- | --- | --- |
| `bc1qyq6hayye9xqhjut6walu7avme5ad9d070x6vjn` | 0.666 | 0.000 | 18 | 3 | 12633.678 |
| `bc1qk72khnsd8229rq3f9mdm9lum25x07tpkqpsaywe745pyk3gyw4nsnj8g03` | 0.665 | 0.034 | 2 | 2 | 572.027 |
| `bc1qhu6ay5glxezuhkggpysa3sphwgtl4duj3l29re` | 0.664 | 0.003 | 3 | 2 | 130.000 |
| `1APqBWjqSJbRGM1kYfQQUSWEGZD3YG8HLB` | 0.662 | 0.006 | 2 | 2 | 408.530 |
| `bc1qg3fvhy0kw5z777hrcph7a47ul2zskju5r5ue7l` | 0.658 | 0.021 | 2 | 2 | 330.000 |
| `35fq4s5znMrHYpuZhBpvtzp51rK1oBXzNj` | 0.656 | 0.000 | 18 | 111 | 4261.283 |
| `bc1qpkddjmhnv7pv93ff0f98q3y4hvch656cvsatfr` | 0.656 | 0.001 | 5 | 2 | 1542.425 |
| `bc1q0a5zgnc08lhw23ddy7p34kkqwvkhhwn0u0nsnc` | 0.656 | 0.001 | 2 | 2 | 185.826 |
| `bc1qrqrefwzvtv50r539hn7umufj84gm2xx9vz7ykw` | 0.651 | 0.001 | 100 | 3 | 3618.962 |
| `bc1qryvmz9lsed80vhwp5h5un05y3g8srcr27dp27p` | 0.649 | 0.032 | 2 | 2 | 148.000 |
| `bc1q7ert6fm0nu2hazzgcmwwjaxtlz4hdvnflk6pkj9w8828pyyjlvfq7wau85` | 0.646 | 0.000 | 6 | 66 | 350.001 |
| `bc1qjhr74pnlr5wd8u4z8ed2zn5l03kax0h5pxymys` | 0.645 | 0.000 | 5 | 9 | 628.000 |
| `36bWP3iwAE2xL64dmFj5zH5Ga1JHHtjBM4` | 0.644 | 0.005 | 2 | 18 | 200.000 |
| `bc1qxazmz7kcm6w65zjcws20cqrvxcz80rrfzu0j82` | 0.643 | 0.000 | 10 | 2 | 573.950 |
| `bc1qhh9qq78l3dd5dnnu0xqlsv9nljem43xe7cl3h4` | 0.641 | 0.002 | 100 | 31 | 7780.677 |

These are leads for a human to look at, not accusations: big batch payers, consolidations and new script types all look unusual against 2016-2017 wallets.

## Rebuild

```
cd backend
..\.venv\Scripts\python.exe -m training.anomaly_check offline
..\.venv\Scripts\python.exe -m training.anomaly_check recent
```
