"""Teach the model ransomware and random-address behaviour, and test it on families it never saw.

Run from backend/ after build_training_dataset (about 30-45 minutes):
    python -m training.ransomware_check

Rows:
  Elliptic++   labelled wallets (1 illicit, 0 licit), as before.
  Ransomware   Ransomwhere addresses not in Elliptic++, rows within 30 days of their payments.
  Population   random active addresses with no known label (presumed licit, so weighted lower).
Split (nothing from the test side is used for training, thresholds, or exposure features):
  train        steps 1-34: Elliptic++ and (model C) population rows; Locky addresses first active before
               step 17 (Locky ran from step 5 to 25, all in 2016), their rows before step 17.
  test         Elliptic++ steps 35-49; Locky addresses first active from step 17 on; every other ransomware
               family (SamSam, WannaCry, NotPetya, Ryuk, Conti, ...) at any step = unseen families.
Exposure features are recomputed with only training-side ransomware labels.
Models (tuned XGBoost, v2 features + exposure):
  A  Elliptic++ labels only (the current model)
  B  + Locky ransomware as illicit
  C  + Locky ransomware + population rows as presumed licit (weight POP_WEIGHT)
Thresholds, both picked on steps 31-34 with a model fit on steps 1-30:
  F1   best illicit F1 on Elliptic++ rows
  1%   flags 1% of random population rows
Writes reports/ransomware_check.md.
"""
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, f1_score, precision_score, recall_score, roc_auc_score
from xgboost import XGBClassifier

from app.features.wallet_window import EXPOSURE_FEATURE_NAMES
from training import exposure
from training.build_training_dataset import (
    BACKEND_DIR, DATASET, OUT_DIR, label_fingerprints, load_elliptic, load_external,
)
from training.compare_models import best_threshold
from training.improvement_check import V2, V2_EXP, XGB_TUNED

REPORT = BACKEND_DIR / "reports" / "ransomware_check.md"
SEED = 0
POP_WEIGHT = 0.5
POP_TRAIN_ROWS = 300_000
POP_TEST_ROWS = 200_000
# Conclusions from the 2026-10-30 run; update them if a rerun changes the numbers.
NOTES = """## What this means

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
"""
TRAIN_FAMILY = "Locky"
LOCKY_SPLIT_STEP = 17


def xgb(y: np.ndarray, w: np.ndarray) -> XGBClassifier:
    params = dict(XGB_TUNED)
    pos, neg = w[y == 1].sum(), w[y == 0].sum()
    ratio = float(neg / max(pos, 1e-9)) * params.pop("pos_weight_factor")
    return XGBClassifier(**params, scale_pos_weight=ratio, tree_method="hist", n_jobs=-1, random_state=SEED)


def fit(rows: pd.DataFrame, cols: list[str]) -> XGBClassifier:
    y, w = rows.y.to_numpy(), rows.w.to_numpy()
    for attempt in range(3):  # retry a known Python 3.15 thread-pool race (not a data problem)
        try:
            return xgb(y, w).fit(rows[cols], y, sample_weight=w)
        except ValueError as exc:
            if "concurrent send_bytes" not in str(exc) or attempt == 2:
                raise


def first_family(text) -> str:
    return str(text).split("|")[0] if isinstance(text, str) and text else "Unlabeled"


def load() -> tuple[pd.DataFrame, pd.DataFrame]:
    columns = ["address", "step", "source", "label", "known_elliptic_class", "external_label", "external_family",
               "external_first_seen", "external_last_seen", "anchor_time"] + V2
    df = pd.read_parquet(DATASET, columns=list(dict.fromkeys(columns)))
    order = pd.read_parquet(OUT_DIR / "wallet_window_features.parquet", columns=["address", "step"])
    if not (order.step.to_numpy() == df.step.to_numpy()).all() or not (order.address.astype(str).to_numpy() == df.address.astype(str).to_numpy()).all():
        raise ValueError("training_dataset.parquet rows are not in the features-file order; rebuild it first")
    return df, order


def main() -> None:
    df, _ = load()
    in_elliptic = df.known_elliptic_class.notna()
    labelled_ext = df.external_label.notna()
    is_ransom = df.external_label.fillna("").str.contains("ransomware") & ~in_elliptic
    anchor = pd.to_datetime(df.anchor_time, utc=True)
    first_seen = pd.to_datetime(df.external_first_seen, errors="coerce", utc=True)
    last_seen = pd.to_datetime(df.external_last_seen, errors="coerce", utc=True)
    is_ransom &= (anchor >= first_seen - pd.Timedelta(days=30)) & (anchor <= last_seen + pd.Timedelta(days=30))
    family = df.external_family.map(first_family)

    locky = is_ransom & (family == TRAIN_FAMILY)
    locky_first = df[locky].groupby("address").step.min()
    locky_train_addresses = set(locky_first[locky_first < LOCKY_SPLIT_STEP].index)
    ransom_train = locky & df.address.isin(locky_train_addresses) & (df.step < LOCKY_SPLIT_STEP)
    locky_test = locky & ~df.address.isin(locky_train_addresses)
    unseen_families = is_ransom & (family != TRAIN_FAMILY)

    # Exposure features again, now with only the training-side ransomware labels.
    print("recomputing exposure features without test-side labels ...", flush=True)
    elliptic_rows, elliptic_class = load_elliptic()
    external = load_external()
    external = external[external.address.isin(locky_train_addresses)]
    steps = pd.read_csv(OUT_DIR / "time_steps.csv", parse_dates=["snap_start_time", "snap_end_time"])
    labels = exposure.label_table(elliptic_rows, elliptic_class, external, steps)
    clean = exposure.exposure_frame(OUT_DIR / "wallet_window_features.parquet", labels,
                                    label_fingerprints(labels["address"].tolist()))
    df[list(EXPOSURE_FEATURE_NAMES)] = clean.astype("float32").to_numpy()
    cols = V2_EXP

    population = (df.source.isin(["bq_same_era", "bq_post"]) & ~in_elliptic & ~labelled_ext)
    rng = np.random.default_rng(SEED)

    def take(mask: pd.Series, n: int) -> pd.DataFrame:
        rows = df[mask]
        return rows.iloc[rng.choice(len(rows), size=min(n, len(rows)), replace=False)] if len(rows) else rows

    elliptic = df[df.label.notna()].assign(y=lambda x: x.label.astype(int), w=1.0)
    ransom_rows = df[ransom_train].assign(y=1, w=1.0)
    pop_train = take(population & (df.step <= 34), POP_TRAIN_ROWS).assign(y=0, w=POP_WEIGHT)
    pop_test = take(population & df.step.between(35, 49), POP_TEST_ROWS).assign(y=0, w=1.0)
    pop_post = take(population & (df.step >= 50), POP_TEST_ROWS).assign(y=0, w=1.0)

    train_sets = {
        "A  Elliptic++ only": [elliptic[elliptic.step <= 34]],
        "B  + Locky ransomware": [elliptic[elliptic.step <= 34], ransom_rows],
        "C  + Locky + random addresses": [elliptic[elliptic.step <= 34], ransom_rows, pop_train],
    }
    e_test = elliptic[elliptic.step.between(35, 49)]
    tests = {
        f"Locky, addresses first active at step {LOCKY_SPLIT_STEP}+": df[locky_test],
        "Unseen families (all other ransomware)": df[unseen_families],
    }
    # Reference for each test: random active addresses from the same steps.
    references = {name: take(population & df.step.isin(set(rows.step)), POP_TEST_ROWS) for name, rows in tests.items()}
    pop_val = take(population & df.step.between(31, 34), 100_000)  # same rows for every model
    results = []
    for name, parts in train_sets.items():
        print(f"training {name} ...", flush=True)
        rows = pd.concat(parts)
        early, late = rows[rows.step <= 30], rows[rows.step > 30]
        probe = fit(early, cols)
        late_e = late[late.source == "elliptic_pp"]
        t_f1 = best_threshold(late_e.y.to_numpy(), probe.predict_proba(late_e[cols])[:, 1])
        t_1pct = float(np.quantile(probe.predict_proba(pop_val[cols])[:, 1], 0.99))
        model = fit(rows, cols)

        p_e = model.predict_proba(e_test[cols])[:, 1]
        p_pop = model.predict_proba(pop_test[cols])[:, 1]
        p_post = model.predict_proba(pop_post[cols])[:, 1]
        row = {"model": name, "t_f1": t_f1, "t_1pct": t_1pct,
               "e_pr": average_precision_score(e_test.y, p_e), "e_roc": roc_auc_score(e_test.y, p_e)}
        for t_name, t in (("f1", t_f1), ("1pct", t_1pct)):
            pred = p_e >= t
            row[f"e_prec_{t_name}"] = precision_score(e_test.y, pred, zero_division=0)
            row[f"e_rec_{t_name}"] = recall_score(e_test.y, pred)
            row[f"e_f1_{t_name}"] = f1_score(e_test.y, pred)
            row[f"pop_{t_name}"] = float((p_pop >= t).mean())
            row[f"post_{t_name}"] = float((p_post >= t).mean())
        for test_name, test_rows in tests.items():
            p = model.predict_proba(test_rows[cols])[:, 1]
            ref = model.predict_proba(references[test_name][cols])[:, 1]
            row[f"{test_name}|rows"] = len(test_rows)
            row[f"{test_name}|roc"] = roc_auc_score(np.r_[np.ones(len(p)), np.zeros(len(ref))], np.r_[p, ref]) if len(p) else np.nan
            row[f"{test_name}|rec_f1"] = float((p >= t_f1).mean()) if len(p) else np.nan
            row[f"{test_name}|rec_1pct"] = float((p >= t_1pct).mean()) if len(p) else np.nan
        results.append(row)
        print({k: (round(v, 3) if isinstance(v, float) else v) for k, v in row.items()}, flush=True)

    fam_counts = df[unseen_families].groupby(family[unseen_families]).address.nunique().sort_values(ascending=False)
    lines = [
        "# Ransomware and random-address training check",
        "",
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by training/ransomware_check.py.",
        f"Train: steps 1-34. Ransomware positives: {int(ransom_train.sum()):,} Locky rows"
        f" ({len(locky_train_addresses):,} addresses). Random addresses: {len(pop_train):,} rows at weight {POP_WEIGHT}.",
        f"Test: Elliptic++ steps 35-49 ({len(e_test):,} rows); Locky addresses first active at step {LOCKY_SPLIT_STEP}+"
        f" ({int(locky_test.sum()):,} rows); unseen families ({int(unseen_families.sum()):,} rows,"
        f" {fam_counts.size} families: {', '.join(fam_counts.index[:8])}...). Exposure features use only training-side",
        "ransomware labels. Model: tuned XGBoost, v2 features + exposure.",
        "",
        "## Elliptic++ test steps 35-49",
        "",
        "| Model | PR-AUC | ROC-AUC | Threshold | Precision | Recall | F1 | Random flagged (35-49) | Random flagged (2017-19) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        for t_name, label in (("f1", "best F1"), ("1pct", "1% random")):
            lines.append(f"| {r['model']} | {r['e_pr']:.3f} | {r['e_roc']:.3f} | {label} ({r['t_' + t_name]:.2f}) |"
                         f" {r['e_prec_' + t_name]:.3f} | {r['e_rec_' + t_name]:.3f} | {r['e_f1_' + t_name]:.3f} |"
                         f" {r['pop_' + t_name]:.1%} | {r['post_' + t_name]:.1%} |")
    lines += [
        "",
        "## Ransomware the model never trained on",
        "",
        "ROC-AUC: ransomware rows against random active addresses of the same steps. Caught: share of ransomware",
        "rows above the threshold.",
        "",
        "| Model | Test | Rows | ROC-AUC | Caught (best-F1 threshold) | Caught (1% threshold) |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        for test_name in tests:
            lines.append(f"| {r['model']} | {test_name} | {r[test_name + '|rows']:,} | {r[test_name + '|roc']:.3f} |"
                         f" {r[test_name + '|rec_f1']:.1%} | {r[test_name + '|rec_1pct']:.1%} |")
    lines.append("")
    lines.append(NOTES)
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
