"""Quick baseline: how well do the live-compatible features separate illicit from licit wallets?

Trains a gradient-boosted tree model on Elliptic++ labelled rows from steps 1-34 and tests on
steps 35-49 (the Elliptic++ paper's time split), then checks how far the 2017-2019 rows drift.
A sanity check, not a tuned model. Run from backend/ after build_training_dataset:

    python -m training.baseline_model

Writes reports/baseline_model.md.
"""
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, f1_score, precision_score, recall_score, roc_auc_score

from app.features.wallet_window import FEATURE_NAMES, NETWORK_FEATURE_NAMES
from training.build_training_dataset import BACKEND_DIR, DATASET, ELLIPTIC_DIR

REPORT = BACKEND_DIR / "reports" / "baseline_model.md"
# Wallet features plus the wallet's fee relative to the network. The two raw network columns
# (tx count and average fee of the last 144 blocks) mostly identify the date, so they are optional.
ERA_COLUMNS = ["net_tx_count_144", "net_avg_fee_btc_144"]
FEATURES = list(FEATURE_NAMES) + [c for c in NETWORK_FEATURE_NAMES if c not in ERA_COLUMNS]
SEED = 0


def model() -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(max_iter=400, learning_rate=0.05, class_weight="balanced",
                                          early_stopping=True, validation_fraction=0.15, random_state=SEED)


def fit(X: pd.DataFrame, y: pd.Series) -> HistGradientBoostingClassifier:
    """Fit, retrying a known thread-pool race in Python 3.15 multiprocessing (not a data problem)."""
    for attempt in range(3):
        try:
            return model().fit(X, y)
        except ValueError as exc:
            if "concurrent send_bytes" not in str(exc) or attempt == 2:
                raise


def best_threshold(y: np.ndarray, p: np.ndarray) -> float:
    """Threshold with the best illicit F1 on the given (validation) data."""
    grid = np.linspace(0.05, 0.95, 91)
    return float(grid[np.argmax([f1_score(y, p >= t) for t in grid])])


def scores(y: np.ndarray, p: np.ndarray, threshold: float) -> dict:
    pred = p >= threshold
    return {"PR-AUC": average_precision_score(y, p), "ROC-AUC": roc_auc_score(y, p),
            "precision": precision_score(y, pred), "recall": recall_score(y, pred), "F1": f1_score(y, pred)}


def fit_and_score(train: pd.DataFrame, test: pd.DataFrame, cols: list[str]) -> tuple[dict, HistGradientBoostingClassifier, float]:
    # Pick the threshold on the last train steps (31-34), then refit on all of 1-34.
    early, late = train[train.step <= 30], train[train.step > 30]
    probe = fit(early[cols], early.label)
    threshold = best_threshold(late.label.to_numpy(), probe.predict_proba(late[cols])[:, 1])
    clf = fit(train[cols], train.label)
    p = clf.predict_proba(test[cols])[:, 1]
    return scores(test.label.to_numpy(), p, threshold), clf, threshold


def original_elliptic_features(rows: pd.DataFrame) -> pd.DataFrame:
    """The Elliptic++ CSV's own wallet features for the same rows (reference only; not live-compatible)."""
    raw = pd.read_csv(ELLIPTIC_DIR / "wallets_features.csv", dtype={"address": "string"})
    raw = raw.rename(columns={"Time step": "step"}).drop_duplicates(["address", "step"])
    return rows[["address", "step", "label"]].merge(raw, on=["address", "step"], how="inner")


def main() -> None:
    df = pd.read_parquet(DATASET)
    df["address"] = df["address"].astype("string")
    labelled = df[df.label.notna()].copy()
    labelled["label"] = labelled.label.astype(int)
    train = labelled[labelled.split == "train"]
    test = labelled[labelled.split == "test"]
    test_new = test[~test.address.isin(set(train.address))]

    results = {}
    results["test steps 35-49"], clf, threshold = fit_and_score(train, test, FEATURES)
    results["test, addresses unseen in training"], _, _ = fit_and_score(train, test_new, FEATURES)
    results["same, plus raw network columns"], _, _ = fit_and_score(train, test, FEATURES + ERA_COLUMNS)

    original = original_elliptic_features(labelled)
    orig_cols = [c for c in original.columns if c not in ("address", "step", "label")]
    results["reference: Elliptic++ CSV features"], _, _ = fit_and_score(
        original[original.step <= 34], original[original.step > 34], orig_cols)

    p_test = clf.predict_proba(test[FEATURES])[:, 1]
    per_step = test.assign(p=p_test).groupby("step").apply(
        lambda g: pd.Series({"illicit": int(g.label.sum()), "licit": int((g.label == 0).sum()),
                             "F1": f1_score(g.label, g.p >= threshold) if g.label.sum() else np.nan}),
        include_groups=False)

    # Drift: can a model tell 2016-17 rows from 2017-19 rows? 0.5 = no difference, 1.0 = fully different.
    era = df[df.source.isin(["elliptic_pp", "bq_same_era"])].sample(200_000, random_state=SEED)
    post = df[df.source == "bq_post"].sample(200_000, random_state=SEED)
    both = pd.concat([era.assign(y=0), post.assign(y=1)])
    holdout = both.sample(frac=0.3, random_state=SEED)
    fit_rows = both.drop(holdout.index)
    drift_auc = roc_auc_score(holdout.y, fit(fit_rows[FEATURES], fit_rows.y).predict_proba(holdout[FEATURES])[:, 1])
    era_cols = FEATURES + ERA_COLUMNS
    drift_auc_era = roc_auc_score(holdout.y, fit(fit_rows[era_cols], fit_rows.y).predict_proba(holdout[era_cols])[:, 1])

    # Addresses that reappear after 2017 with their old Elliptic++ class (a weak, carried-forward label).
    carried = df[(df.source == "bq_post") & df.known_elliptic_class.isin([1, 2])]
    carried_y = (carried.known_elliptic_class == 1).astype(int).to_numpy()
    carried_p = clf.predict_proba(carried[FEATURES])[:, 1]
    flagged_post = float((clf.predict_proba(post[FEATURES])[:, 1] >= threshold).mean())
    flagged_test = float((p_test >= threshold).mean())

    table = ["| Evaluation | Rows (illicit) | PR-AUC | ROC-AUC | Precision | Recall | F1 |", "| --- | --- | --- | --- | --- | --- | --- |"]
    sizes = {"test steps 35-49": test, "test, addresses unseen in training": test_new,
             "same, plus raw network columns": test, "reference: Elliptic++ CSV features": original[original.step > 34]}
    for name, r in results.items():
        n = sizes[name]
        table.append(f"| {name} | {len(n):,} ({int(n.label.sum()):,}) | {r['PR-AUC']:.3f} | {r['ROC-AUC']:.3f} | "
                     f"{r['precision']:.3f} | {r['recall']:.3f} | {r['F1']:.3f} |")
    step_lines = ["| Step | Illicit | Licit | F1 |", "| --- | --- | --- | --- |"]
    step_lines += [f"| {s} | {int(r.illicit)} | {int(r.licit)} | {r.F1:.3f} |" for s, r in per_step.iterrows()]

    lines = [
        "# Baseline model check",
        "",
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by training/baseline_model.py. Not a tuned model.",
        "",
        "Model: scikit-learn HistGradientBoostingClassifier, balanced class weights, trained on Elliptic++ labelled",
        f"rows from steps 1-34 ({len(train):,} rows, {int(train.label.sum()):,} illicit). Decision threshold"
        f" {threshold:.2f}, picked on steps 31-34. Illicit is the positive class; Elliptic++ 'unknown' rows are excluded.",
        "",
        "## Illicit detection on later steps",
        "",
        *table,
        "",
        "The first rows use the live-compatible features from training_dataset.parquet: the wallet features plus",
        "fee_vs_network_mean. net_tx_count_144 and net_avg_fee_btc_144 are left out by default because they mostly",
        "say *when* a row is from (see Drift), which does not carry over to new periods. The reference row uses",
        "the Elliptic++ CSV's own wallet features on the same split; those cannot be computed from live data.",
        "",
        "## By test step",
        "",
        *step_lines,
        "",
        "## Drift after 2017",
        "",
        f"- A model telling 2016-17 rows from 2017-19 rows reaches ROC-AUC {drift_auc:.3f} with the default features"
        f" and {drift_auc_era:.3f} with the raw network columns added (0.5 = no difference, 1.0 = always tells them apart).",
        f"- Share of rows flagged illicit: {flagged_test:.1%} on test steps 35-49, {flagged_post:.1%} on 2017-19 rows.",
        f"- {len(carried):,} post-2017 rows belong to addresses with an Elliptic++ class ({int(carried_y.sum()):,} illicit)."
        + (f" The model's ROC-AUC against that old class is {roc_auc_score(carried_y, carried_p):.3f}"
           " (a weak check: the class is from 2016-17)." if 0 < carried_y.sum() < len(carried_y) else ""),
        "",
    ]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
