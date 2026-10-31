"""Do the new features help? Tuned XGBoost / Random Forest on three feature sets, plus outside checks.

Run from backend/ after build_training_dataset (v2 features), about 30-60 minutes:
    python -m training.improvement_check

Feature sets:
  v1        the original window features + fee_vs_network_mean (as in model_comparison.md)
  v2        + transaction shape, round amounts, 1-week window, address type
  v2+exp    + exposure to known-illicit / known-licit counterparties (labels known before the step)
Checks:
  1. Elliptic++ split: train steps 1-34 once, test steps 35-49.
  2. Retraining before each test step (best set).
  3. Outside labels Elliptic++ never used: Ransomwhere addresses (2016-2019) and Elliptic++ addresses seen
     again in 2017-2019, against random active addresses of the same steps. Model trained on steps 1-49.
Writes reports/improvement_check.md.
"""
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score
from xgboost import XGBClassifier

from app.features.wallet_window import ADDRESS_FEATURE_NAMES, EXPOSURE_FEATURE_NAMES, FEATURE_NAMES, STAT_SUFFIXES
from training.build_training_dataset import BACKEND_DIR, DATASET
from training.compare_models import best_threshold, metrics
from training.tune_models import fit_retry, rf
from training.xgb_device import xgb_device

REPORT = BACKEND_DIR / "reports" / "improvement_check.md"
SEED = 0
V1_FAMILIES = ("btc_transacted", "btc_sent", "btc_received", "fees", "fees_as_share", "blocks_btwn_txs",
               "blocks_btwn_input_txs", "blocks_btwn_output_txs", "transacted_w_address")
V1 = list(FEATURE_NAMES[:10]) + [f"{f}_{s}" for f in V1_FAMILIES for s in STAT_SUFFIXES] + ["fee_vs_network_mean"]
V2 = list(FEATURE_NAMES) + list(ADDRESS_FEATURE_NAMES) + ["fee_vs_network_mean"]
V2_EXP = V2 + list(EXPOSURE_FEATURE_NAMES)
SETS = {"v1": V1, "v2": V2, "v2+exp": V2_EXP}
# Best settings from reports/tuning.md (tuned on steps 1-34 only).
XGB_TUNED = {"subsample": 0.85, "reg_lambda": 20, "pos_weight_factor": 0.25, "n_estimators": 600,
             "min_child_weight": 5, "max_depth": 4, "learning_rate": 0.1, "colsample_bytree": 0.9}
RF_TUNED = {"n_estimators": 300, "min_samples_leaf": 3, "max_features": "sqrt", "max_depth": None}
# Conclusions from the 2026-10-30 run; update them if a rerun changes the numbers.
NOTES = [
    "## What this means",
    "",
    "- New transaction features and exposure to known labels are the big gains: tuned XGBoost goes from PR-AUC",
    "  0.446 (v1) to 0.663 (v2+exp) when trained once, and 0.753 when retrained before each step. Steps 46-49,",
    "  where the original model failed, reach PR-AUC 0.639 (training/train_models.py holdout).",
    "- Exposure uses only labels known before each row's step. In the test that includes labels from earlier",
    "  test steps, which matches a deployment where new labels keep arriving.",
    "- Caveat 1, threshold: at 0.19 the model flags 28.5% of random 2016-17 addresses and 12.5% of 2017-19 ones.",
    "  Elliptic++ is not a random sample of addresses, so live use needs a stricter threshold, or training with",
    "  random active addresses as presumed-licit rows.",
    "- Caveat 2, ransomware: Elliptic++ illicit behaviour is not ransomware behaviour (ROC-AUC 0.64 in 2016-17,",
    "  0.55 in 2017-19). Next step: add the Ransomwhere rows as training positives and test on held-out families.",
    "",
]


def xgb_tuned(_params: dict, y: pd.Series) -> XGBClassifier:
    params = dict(XGB_TUNED)
    ratio = float((y == 0).sum() / max((y == 1).sum(), 1)) * params.pop("pos_weight_factor")
    return XGBClassifier(**params, scale_pos_weight=ratio, tree_method="hist", device=xgb_device(), n_jobs=-1, random_state=SEED)


MODELS = {"XGBoost (tuned)": (xgb_tuned, {}), "Random Forest (tuned)": (rf, RF_TUNED)}


def train(builder, params, rows: pd.DataFrame, cols: list[str]):
    return fit_retry(lambda: builder(params, rows.label), rows[cols], rows.label)


def train_with_threshold(builder, params, rows: pd.DataFrame, cols: list[str]):
    """Threshold from the last four steps (model fit on the earlier ones), then refit on everything."""
    last = rows.step.max()
    early, late = rows[rows.step <= last - 4], rows[rows.step > last - 4]
    probe = train(builder, params, early, cols)
    threshold = best_threshold(late.label.to_numpy(), probe.predict_proba(late[cols])[:, 1])
    return train(builder, params, rows, cols), threshold


def outside_check(clf, cols: list[str], positives: pd.DataFrame, population: pd.DataFrame, threshold: float) -> dict:
    """Known-bad rows against random active rows of the same period (mostly, not certainly, licit)."""
    p_pos = clf.predict_proba(positives[cols])[:, 1]
    p_pop = clf.predict_proba(population[cols])[:, 1]
    y = np.r_[np.ones(len(p_pos)), np.zeros(len(p_pop))]
    p = np.r_[p_pos, p_pop]
    return {"rows": len(positives), "ROC-AUC": roc_auc_score(y, p),
            "flagged": float((p_pos >= threshold).mean()), "flagged_random": float((p_pop >= threshold).mean())}


def main() -> None:
    columns = ["address", "step", "source", "label", "known_elliptic_class", "external_label",
               "external_first_seen", "external_last_seen", "anchor_time"] + V2_EXP
    df = pd.read_parquet(DATASET, columns=list(dict.fromkeys(columns)))
    labelled = df[df.label.notna()].copy()
    labelled["label"] = labelled.label.astype(int)
    train_rows, test_rows = labelled[labelled.step <= 34], labelled[labelled.step.between(35, 49)]
    y_test = test_rows.label.to_numpy()

    # 1. Static split.
    static = []
    for model_name, (builder, params) in MODELS.items():
        for set_name, cols in SETS.items():
            print(f"{model_name} on {set_name} ...", flush=True)
            clf, threshold = train_with_threshold(builder, params, train_rows, cols)
            static.append((model_name, set_name, metrics(y_test, clf.predict_proba(test_rows[cols])[:, 1], threshold)))
    best_model, best_set, _ = max(static, key=lambda r: r[2]["PR-AUC"])
    builder, params = MODELS[best_model]
    cols = SETS[best_set]

    # 2. Retraining before each test step.
    print(f"retraining before each step with {best_model} on {best_set} ...", flush=True)
    rolling = np.zeros(len(test_rows))
    for step in range(35, 50):
        clf = train(builder, params, labelled[labelled.step < step], cols)
        mask = (test_rows.step == step).to_numpy()
        rolling[mask] = clf.predict_proba(test_rows.loc[mask, cols])[:, 1]
    rolling_pr = average_precision_score(y_test, rolling)

    # 3. Outside labels, model trained on every Elliptic++ label (steps 1-49).
    print("outside checks ...", flush=True)
    final, final_threshold = train_with_threshold(builder, params, labelled, cols)
    in_elliptic = df.known_elliptic_class.notna()
    ransom = df.external_label.fillna("").str.contains("ransomware") & ~in_elliptic
    seen_from = pd.to_datetime(df.external_first_seen, errors="coerce", utc=True) - pd.Timedelta(days=30)
    seen_to = pd.to_datetime(df.external_last_seen, errors="coerce", utc=True) + pd.Timedelta(days=30)
    anchor = pd.to_datetime(df.anchor_time, utc=True)
    ransom_active = ransom & (anchor >= seen_from) & (anchor <= seen_to)
    random_era = df[(df.source == "bq_same_era") & ~in_elliptic & df.external_label.isna()]
    random_post = df[(df.source == "bq_post") & ~in_elliptic & df.external_label.isna()]
    random_era = random_era.sample(min(len(random_era), 200_000), random_state=SEED)
    random_post = random_post.sample(min(len(random_post), 200_000), random_state=SEED)
    checks = []
    era_ransom = df[ransom_active & (df.step <= 49)]
    post_ransom = df[ransom_active & (df.step >= 50)]
    if len(era_ransom):
        checks.append(("Ransomwhere addresses, 2016-17 (not in Elliptic++)", outside_check(final, cols, era_ransom, random_era, final_threshold)))
    if len(post_ransom):
        checks.append(("Ransomwhere addresses, 2017-19", outside_check(final, cols, post_ransom, random_post, final_threshold)))
    carried_illicit = df[(df.step >= 50) & (df.known_elliptic_class == 1)]
    if len(carried_illicit):
        checks.append(("Elliptic++ illicit addresses seen again in 2017-19", outside_check(final, cols, carried_illicit, random_post, final_threshold)))
    carried = df[(df.step >= 50) & df.known_elliptic_class.isin([1, 2])]
    carried_auc = None
    if carried.known_elliptic_class.nunique() == 2:
        carried_auc = roc_auc_score((carried.known_elliptic_class == 1).astype(int), final.predict_proba(carried[cols])[:, 1])

    lines = [
        "# Do the new features help?",
        "",
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by training/improvement_check.py.",
        "Models use the tuned settings from reports/tuning.md. Feature sets: v1 = original window features;",
        "v2 = + transaction shape, round amounts, 1-week window, address type; v2+exp = + exposure to known",
        "illicit/licit counterparties (only labels known before the row's step).",
        "",
        "## 1. Train on steps 1-34 once, test on steps 35-49",
        "",
        "| Model | Features | PR-AUC | ROC-AUC | Precision | Recall | F1 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        *[f"| {m} | {f} | {r['PR-AUC']:.3f} | {r['ROC-AUC']:.3f} | {r['precision']:.3f} | {r['recall']:.3f} | {r['F1']:.3f} |"
          for m, f, r in static],
        "",
        f"## 2. Retraining before each test step ({best_model}, {best_set})",
        "",
        f"PR-AUC on steps 35-49: {rolling_pr:.3f} (trained once: {max(r[2]['PR-AUC'] for r in static):.3f}).",
        "",
        f"## 3. Labels Elliptic++ never used ({best_model}, {best_set}, trained on steps 1-49)",
        "",
        "Known-bad rows scored against random active addresses of the same period. Random addresses are mostly",
        "but not certainly licit, so these numbers are a lower bound. 'Flagged' uses the model's threshold"
        f" ({final_threshold:.2f}).",
        "",
        "| Check | Rows | ROC-AUC | Flagged | Random flagged |",
        "| --- | --- | --- | --- | --- |",
        *[f"| {name} | {c['rows']:,} | {c['ROC-AUC']:.3f} | {c['flagged']:.1%} | {c['flagged_random']:.1%} |" for name, c in checks],
        "",
    ]
    if carried_auc is not None:
        lines += [f"Elliptic++ illicit vs licit addresses seen again in 2017-19 ({len(carried):,} rows): ROC-AUC {carried_auc:.3f}.", ""]
    lines += NOTES
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
