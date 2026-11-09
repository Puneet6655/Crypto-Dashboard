"""Free improvements: tune XGBoost and Random Forest, add address type, try an ensemble.

Run from backend/ after build_training_dataset (about 20-40 minutes on a laptop):
    python -m training.tune_models

Tuning uses only training steps: fit on 1-26, score PR-AUC on 27-34. The chosen settings are
then refit on 1-34 and tested once on 35-49, so the test numbers compare fairly with
reports/model_comparison.md. Writes reports/tuning.md.
"""
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import average_precision_score
from sklearn.model_selection import ParameterSampler
from xgboost import XGBClassifier

from training.baseline_model import FEATURES
from training.build_training_dataset import BACKEND_DIR, DATASET
from training.compare_models import best_threshold, metrics
from training.xgb_device import xgb_device

REPORT = BACKEND_DIR / "reports" / "tuning.md"
SEED = 0
ADDRESS_TYPE = ["addr_p2pkh", "addr_p2sh", "addr_bech32"]
XGB_SPACE = {
    "n_estimators": [300, 600, 1000], "learning_rate": [0.02, 0.05, 0.1], "max_depth": [4, 6, 8, 10],
    "min_child_weight": [1, 5, 20], "subsample": [0.7, 0.85, 1.0], "colsample_bytree": [0.5, 0.7, 0.9],
    "reg_lambda": [1, 5, 20], "pos_weight_factor": [0.25, 0.5, 1.0],
}
RF_SPACE = {"n_estimators": [300], "max_features": ["sqrt", 0.3, 0.5], "min_samples_leaf": [1, 3, 10],
            "max_depth": [None, 20]}


def add_address_type(df: pd.DataFrame) -> pd.DataFrame:
    """One-hot script type from the address text. Live lookups can do the same."""
    address = df["address"].astype(str)
    df["addr_p2pkh"] = address.str.startswith("1").astype("int8")
    df["addr_p2sh"] = address.str.startswith("3").astype("int8")
    df["addr_bech32"] = address.str.startswith("bc1").astype("int8")
    return df


def xgb(params: dict, y: pd.Series) -> XGBClassifier:
    params = dict(params)
    ratio = float((y == 0).sum() / max((y == 1).sum(), 1)) * params.pop("pos_weight_factor")
    return XGBClassifier(**params, scale_pos_weight=ratio, tree_method="hist", device=xgb_device(), n_jobs=-1, random_state=SEED,
                         eval_metric="aucpr")


def rf(params: dict, y: pd.Series) -> RandomForestClassifier:
    return RandomForestClassifier(**params, class_weight="balanced_subsample", n_jobs=-1, random_state=SEED)


def fit_retry(make, X, y):
    """Fit, retrying a known thread-pool race in Python 3.15 multiprocessing (not a data problem)."""
    for attempt in range(3):
        try:
            return make().fit(X, y)
        except ValueError as exc:
            if "concurrent send_bytes" not in str(exc) or attempt == 2:
                raise


def search(builder, space: dict, n: int, fit_rows: pd.DataFrame, val_rows: pd.DataFrame, cols: list[str]) -> tuple[dict, float]:
    best, best_score = None, -1.0
    for i, params in enumerate(ParameterSampler(space, n_iter=n, random_state=SEED), start=1):
        clf = fit_retry(lambda: builder(params, fit_rows.label), fit_rows[cols], fit_rows.label)
        score = average_precision_score(val_rows.label, clf.predict_proba(val_rows[cols])[:, 1])
        print(f"  {i:2d}/{n} PR-AUC {score:.3f} {params}", flush=True)
        if score > best_score:
            best, best_score = params, score
    return best, best_score


def evaluate(builder, params: dict, train: pd.DataFrame, test: pd.DataFrame, cols: list[str]) -> tuple[dict, np.ndarray, float]:
    """Threshold from steps 31-34 (model fit on 1-30), then refit on 1-34 and score 35-49."""
    early, late = train[train.step <= 30], train[train.step > 30]
    probe = fit_retry(lambda: builder(params, early.label), early[cols], early.label)
    threshold = best_threshold(late.label.to_numpy(), probe.predict_proba(late[cols])[:, 1])
    clf = fit_retry(lambda: builder(params, train.label), train[cols], train.label)
    p = clf.predict_proba(test[cols])[:, 1]
    late_p = probe.predict_proba(late[cols])[:, 1]
    return metrics(test.label.to_numpy(), p, threshold), p, threshold, late_p


def main() -> None:
    df = pd.read_parquet(DATASET, columns=["address", "step", "source", "label"] + FEATURES,
                         filters=[("source", "==", "elliptic_pp")])
    df = add_address_type(df[df.label.notna()].copy())
    df["label"] = df.label.astype(int)
    train, test = df[df.step <= 34], df[(df.step > 34) & (df.step <= 49)]
    fit_rows, val_rows = train[train.step <= 26], train[train.step > 26]
    with_type = FEATURES + ADDRESS_TYPE

    print("tuning XGBoost ...", flush=True)
    xgb_params, xgb_val = search(xgb, XGB_SPACE, 25, fit_rows, val_rows, with_type)
    print("tuning Random Forest ...", flush=True)
    rf_params, rf_val = search(rf, RF_SPACE, 8, fit_rows, val_rows, with_type)

    default_xgb = {"n_estimators": 600, "learning_rate": 0.05, "max_depth": 6, "min_child_weight": 1,
                   "subsample": 0.8, "colsample_bytree": 0.8, "reg_lambda": 1, "pos_weight_factor": 1.0}
    rows = []
    print("testing on steps 35-49 ...", flush=True)
    r, _, _, _ = evaluate(xgb, default_xgb, train, test, FEATURES)
    rows.append(("XGBoost, default (as in model_comparison.md)", r))
    r, _, _, _ = evaluate(xgb, xgb_params, train, test, FEATURES)
    rows.append(("XGBoost, tuned", r))
    r, p_xgb, _, late_xgb = evaluate(xgb, xgb_params, train, test, with_type)
    rows.append(("XGBoost, tuned + address type", r))
    r, p_rf, _, late_rf = evaluate(rf, rf_params, train, test, with_type)
    rows.append(("Random Forest, tuned + address type", r))
    late = train[train.step > 30]
    blend_threshold = best_threshold(late.label.to_numpy(), (late_xgb + late_rf) / 2)
    rows.append(("Ensemble: average of the two above", metrics(test.label.to_numpy(), (p_xgb + p_rf) / 2, blend_threshold)))

    lines = [
        "# Tuning, address type and ensemble",
        "",
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by training/tune_models.py.",
        "Tuning used only steps 1-34 (fit 1-26, score 27-34). Test: steps 35-49, trained once on 1-34",
        "(same split as reports/model_comparison.md). Thresholds picked on steps 31-34.",
        "",
        "| Model | PR-AUC | ROC-AUC | Precision | Recall | F1 |",
        "| --- | --- | --- | --- | --- | --- |",
        *[f"| {name} | {m['PR-AUC']:.3f} | {m['ROC-AUC']:.3f} | {m['precision']:.3f} | {m['recall']:.3f} | {m['F1']:.3f} |"
          for name, m in rows],
        "",
        f"Best XGBoost settings (validation PR-AUC {xgb_val:.3f}): `{xgb_params}`",
        "",
        f"Best Random Forest settings (validation PR-AUC {rf_val:.3f}): `{rf_params}`",
        "",
        "Address type is read from the address text (1... P2PKH, 3... P2SH, bc1... SegWit), so live lookups get it free.",
        "",
    ]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
