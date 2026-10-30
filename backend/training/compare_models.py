"""Compare XGBoost, Random Forest and the baseline, and test two fixes for drift.

Run from backend/ after build_training_dataset (takes 10-20 minutes):
    python -m training.compare_models

All models train on Elliptic++ labelled rows from steps 1-34 and are tested on steps 35-49.
Fix 1 (data drift): rank every feature within its time step against all active addresses of
  that step, so values are "how unusual is this wallet compared with everyone active then",
  not raw BTC amounts or fee levels that change with price and era.
Fix 2 (new illicit behaviour, e.g. after step 43): retrain before each step on every label
  known so far, instead of training once.
Writes reports/model_comparison.md.
"""
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.metrics import average_precision_score, f1_score, precision_score, recall_score, roc_auc_score
from xgboost import XGBClassifier

from training.baseline_model import FEATURES
from training.build_training_dataset import BACKEND_DIR, DATASET

REPORT = BACKEND_DIR / "reports" / "model_comparison.md"
SEED = 0
# Conclusions from the 2026-10-30 run; update them if a rerun changes the numbers.
PLAN = [
    "## What solves the drift problem",
    "",
    "1. Retrain whenever new labels arrive. Section 4 shows it: PR-AUC on steps 35-49 rises from 0.353 to 0.500,",
    "   and step 49 F1 from 0.27 to 0.64. It cannot fix steps 45-47 at once: there the illicit behaviour was new,",
    "   and no earlier label shows it.",
    "2. Get recent labels. This is the only real fix for scoring today's addresses. Public sources: OFAC sanctioned",
    "   Bitcoin addresses, ransomware payment lists (for example Ransomwhere), plus outcomes of this dashboard's own",
    "   investigations. BigQuery computes the same features for any address at any date cheaply (a few GB per",
    "   snapshot thanks to the block filter), so new labels can join the training table directly.",
    "3. Watch drift on live lookups: run the 2016-17 vs now classifier on a batch of live feature rows, and show",
    "   scores as experimental while it can tell them apart. Re-pick the threshold on recent labelled data.",
    "4. Not a fix: ranking features within each step (Section 2). Untested ideas: amounts in USD instead of BTC,",
    "   address-type features.",
    "",
    "Saved models (training/train_models.py): backend/models/xgboost.joblib and random_forest.joblib, trained",
    "on all labelled steps 1-49, with thresholds in backend/models/model_card.json.",
    "",
]


def make_model(name: str, pos_weight: float):
    if name == "XGBoost":
        return XGBClassifier(n_estimators=600, learning_rate=0.05, max_depth=6, subsample=0.8, colsample_bytree=0.8,
                             scale_pos_weight=pos_weight, tree_method="hist", n_jobs=-1, random_state=SEED,
                             eval_metric="aucpr")
    if name == "Random Forest":
        return RandomForestClassifier(n_estimators=300, min_samples_leaf=3, max_features="sqrt",
                                      class_weight="balanced_subsample", n_jobs=-1, random_state=SEED)
    return HistGradientBoostingClassifier(max_iter=400, learning_rate=0.05, class_weight="balanced",
                                          early_stopping=True, validation_fraction=0.15, random_state=SEED)


def fit(name: str, X: pd.DataFrame, y: pd.Series):
    """Fit, retrying a known thread-pool race in Python 3.15 multiprocessing (not a data problem)."""
    for attempt in range(3):
        try:
            return make_model(name, float((y == 0).sum() / max((y == 1).sum(), 1))).fit(X, y)
        except ValueError as exc:
            if "concurrent send_bytes" not in str(exc) or attempt == 2:
                raise


def best_threshold(y: np.ndarray, p: np.ndarray) -> float:
    grid = np.linspace(0.02, 0.98, 97)
    return float(grid[np.argmax([f1_score(y, p >= t, zero_division=0) for t in grid])])


def metrics(y: np.ndarray, p: np.ndarray, threshold: float) -> dict:
    pred = p >= threshold
    return {"PR-AUC": average_precision_score(y, p), "ROC-AUC": roc_auc_score(y, p),
            "precision": precision_score(y, pred, zero_division=0), "recall": recall_score(y, pred),
            "F1": f1_score(y, pred)}


def train_once(name: str, train: pd.DataFrame, cols: list[str]):
    """Threshold picked on steps (last 4 of train), then refit on all of train."""
    last = train.step.max()
    early, late = train[train.step <= last - 4], train[train.step > last - 4]
    probe = fit(name, early[cols], early.label)
    threshold = best_threshold(late.label.to_numpy(), probe.predict_proba(late[cols])[:, 1])
    return fit(name, train[cols], train.label), threshold


def drift_auc(df: pd.DataFrame, cols: list[str]) -> float:
    """How easily a model tells 2016-17 rows from 2017-19 rows (0.5 = same, 1.0 = always)."""
    era = df[df.period == "elliptic"].sample(150_000, random_state=SEED)
    post = df[df.period == "post"].sample(150_000, random_state=SEED)
    both = pd.concat([era.assign(y=0), post.assign(y=1)])
    holdout = both.sample(frac=0.3, random_state=SEED)
    rest = both.drop(holdout.index)
    clf = fit("XGBoost", rest[cols], rest.y)
    return roc_auc_score(holdout.y, clf.predict_proba(holdout[cols])[:, 1])


def table(rows: list[tuple[str, int, int, dict]]) -> list[str]:
    out = ["| Model | Features | PR-AUC | ROC-AUC | Precision | Recall | F1 |", "| --- | --- | --- | --- | --- | --- | --- |"]
    out += [f"| {m} | {f} | {r['PR-AUC']:.3f} | {r['ROC-AUC']:.3f} | {r['precision']:.3f} | {r['recall']:.3f} | {r['F1']:.3f} |"
            for m, f, r in rows]
    return out


def main() -> None:
    # Only the columns needed (the address strings alone take about 1 GB of memory).
    df = pd.read_parquet(DATASET, columns=["step", "period", "source", "split", "label", "known_elliptic_class"] + FEATURES)
    print("ranking features within each time step ...", flush=True)
    ranked_cols = [f"rank_{c}" for c in FEATURES]
    steps = df.groupby("step")
    for col, ranked in zip(FEATURES, ranked_cols):  # one column at a time to keep memory low
        df[ranked] = steps[col].rank(pct=True).astype("float32")

    labelled = df[df.label.notna()].copy()
    labelled["label"] = labelled.label.astype(int)
    train = labelled[labelled.split == "train"]
    test = labelled[labelled.split == "test"]
    carried = df[(df.source == "bq_post") & df.known_elliptic_class.isin([1, 2])]
    carried_y = (carried.known_elliptic_class == 1).astype(int).to_numpy()

    feature_sets = {"raw": FEATURES, "ranked within step": ranked_cols}
    rows, carried_rows, kept = [], [], {}
    for name in ("Baseline (HistGB)", "XGBoost", "Random Forest"):
        for set_name, cols in feature_sets.items():
            print(f"training {name} on {set_name} features ...", flush=True)
            clf, threshold = train_once(name, train, cols)
            p = clf.predict_proba(test[cols])[:, 1]
            rows.append((name, set_name, metrics(test.label.to_numpy(), p, threshold)))
            carried_rows.append((name, set_name, roc_auc_score(carried_y, clf.predict_proba(carried[cols])[:, 1]),
                                 average_precision_score(carried_y, clf.predict_proba(carried[cols])[:, 1])))
            kept[(name, set_name)] = (clf, threshold, p)

    # Pick the best static model by test PR-AUC for the rolling retraining comparison.
    best_name, best_set, _ = max(rows, key=lambda r: r[2]["PR-AUC"])
    cols = feature_sets[best_set]
    _, static_threshold, static_p = kept[(best_name, best_set)]
    print(f"rolling retraining with {best_name} on {best_set} features ...", flush=True)
    rolling_p = np.zeros(len(test))
    per_step = []
    for step in sorted(test.step.unique()):
        seen = labelled[labelled.step < step]
        clf, threshold = train_once(best_name, seen, cols)
        mask = (test.step == step).to_numpy()
        rolling_p[mask] = clf.predict_proba(test.loc[mask, cols])[:, 1]
        y = test.label.to_numpy()[mask]
        per_step.append((step, int(y.sum()), f1_score(y, static_p[mask] >= static_threshold),
                         f1_score(y, rolling_p[mask] >= threshold),
                         average_precision_score(y, static_p[mask]), average_precision_score(y, rolling_p[mask])))
        print(f"  step {step}: static F1 {per_step[-1][2]:.3f}, retrained F1 {per_step[-1][3]:.3f}", flush=True)
    y_all = test.label.to_numpy()

    print("measuring drift ...", flush=True)
    drift_raw = drift_auc(df, FEATURES)
    drift_rank = drift_auc(df, ranked_cols)

    lines = [
        "# Model comparison and drift fixes",
        "",
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by training/compare_models.py.",
        f"Train: Elliptic++ labelled rows, steps 1-34 ({len(train):,} rows, {int(train.label.sum()):,} illicit)."
        f" Test: steps 35-49 ({len(test):,} rows, {int(test.label.sum()):,} illicit). Illicit is the positive class;",
        "the decision threshold is picked on the last four training steps. Features: the live-compatible set",
        "(wallet features + fee_vs_network_mean). Default settings, no tuning.",
        "",
        "## 1. XGBoost, Random Forest and the baseline (train once)",
        "",
        *table(rows),
        "",
        "\"ranked within step\" = each feature replaced by its percentile among all active addresses in the same",
        "time step (Fix 1). Live, the reference would be a recent sample of active addresses.",
        "",
        "## 2. Data drift: can a model tell 2016-17 rows from 2017-19 rows?",
        "",
        f"- Raw features: ROC-AUC {drift_raw:.3f}",
        f"- Ranked within step: ROC-AUC {drift_rank:.3f}",
        "",
        "0.5 means the two periods look the same to a model; 1.0 means they are always told apart.",
"Ranking leaks the time step: tied values (for example every 0 in a yes/no column) get a percentile that is",
        "different in every step, so a model can read the step from them. This fix, as built, does not work.",
        "",
        "## 3. Known Elliptic++ addresses seen again in 2017-19",
        "",
        f"{len(carried):,} rows ({int(carried_y.sum()):,} with an illicit Elliptic++ class). A weak check: the class is from",
        "2016-17 and an address may have changed use, but it is the only labelled signal after 2017.",
        "",
        "| Model | Features | ROC-AUC | PR-AUC |",
        "| --- | --- | --- | --- |",
        *[f"| {m} | {f} | {auc:.3f} | {ap:.3f} |" for m, f, auc, ap in carried_rows],
        "",
        f"## 4. Retraining as labels arrive ({best_name}, {best_set} features)",
        "",
        "Static: trained once on steps 1-34. Retrained: before each test step, trained on every earlier step.",
        "",
        f"- All test steps, PR-AUC: static {average_precision_score(y_all, static_p):.3f},"
        f" retrained {average_precision_score(y_all, rolling_p):.3f}",
        "",
        "| Step | Illicit | F1 static | F1 retrained | PR-AUC static | PR-AUC retrained |",
        "| --- | --- | --- | --- | --- | --- |",
        *[f"| {s} | {n} | {a:.3f} | {b:.3f} | {c:.3f} | {d:.3f} |" for s, n, a, b, c, d in per_step],
        "",
        *PLAN,
    ]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
