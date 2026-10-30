# Tuning, address type and ensemble

Generated 2026-10-30 08:50 UTC by training/tune_models.py.
Tuning used only steps 1-34 (fit 1-26, score 27-34). Test: steps 35-49, trained once on 1-34
(same split as reports/model_comparison.md). Thresholds picked on steps 31-34.

| Model | PR-AUC | ROC-AUC | Precision | Recall | F1 |
| --- | --- | --- | --- | --- | --- |
| XGBoost, default (as in model_comparison.md) | 0.353 | 0.862 | 0.464 | 0.295 | 0.361 |
| XGBoost, tuned | 0.453 | 0.868 | 0.611 | 0.395 | 0.480 |
| XGBoost, tuned + address type | 0.451 | 0.871 | 0.641 | 0.374 | 0.473 |
| Random Forest, tuned + address type | 0.301 | 0.798 | 0.610 | 0.194 | 0.295 |
| Ensemble: average of the two above | 0.398 | 0.851 | 0.558 | 0.327 | 0.412 |

Best XGBoost settings (validation PR-AUC 0.688): `{'subsample': 0.85, 'reg_lambda': 20, 'pos_weight_factor': 0.25, 'n_estimators': 600, 'min_child_weight': 5, 'max_depth': 4, 'learning_rate': 0.1, 'colsample_bytree': 0.9}`

Best Random Forest settings (validation PR-AUC 0.642): `{'n_estimators': 300, 'min_samples_leaf': 3, 'max_features': 'sqrt', 'max_depth': None}`

Address type is read from the address text (1... P2PKH, 3... P2SH, bc1... SegWit), so live lookups get it free.
