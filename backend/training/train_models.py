"""Train the XGBoost and Random Forest wallet models on every Elliptic++ label and save them.

Run from backend/ after build_training_dataset:
    python -m training.train_models

The decision threshold is picked by training on steps 1-45 and choosing the best illicit F1 on
steps 46-49; the saved model is then refit on all labelled steps 1-49 (retrain when new labels
arrive: reports/model_comparison.md shows that matters). Saves backend/models/ (gitignored):
  xgboost.joblib, random_forest.joblib, and model_card.json with features, thresholds and scores.
Features: wallet window + transaction shape + address type + fee_vs_network_mean + exposure (see
training/improvement_check.py, V2_EXP). Score live addresses with
app.features.wallet_window.live_wallet_features(..., illicit=..., licit=...) and the same columns.
"""
import json
from datetime import datetime, timezone

import joblib
import pandas as pd
from sklearn.metrics import average_precision_score, f1_score

from training.build_training_dataset import BACKEND_DIR, DATASET
from training.compare_models import best_threshold
from training.improvement_check import MODELS, V2_EXP as FEATURES, train
from training.ransomware_check import fit as fit_weighted

MODELS_DIR = BACKEND_DIR / "models"
FILES = {"XGBoost (tuned)": "xgboost.joblib", "Random Forest (tuned)": "random_forest.joblib"}
HOLDOUT_FROM_STEP = 46
ALL_LABELS_FILE = "xgboost_all_labels.joblib"
POP_ROWS = 400_000
POP_WEIGHT = 0.5


def train_all_labels(path) -> dict:
    """Recommended for live use: Elliptic++ + every Ransomwhere family + random active addresses as
    presumed licit (weight POP_WEIGHT). See reports/ransomware_check.md for why. Two thresholds:
    best F1 on Elliptic++ steps 46-49, and the score that flags 1% of random addresses from step 46 on."""
    cols = ["address", "step", "source", "label", "known_elliptic_class", "external_label",
            "external_first_seen", "external_last_seen", "anchor_time"]
    df = pd.read_parquet(DATASET, columns=list(dict.fromkeys(cols + FEATURES)))
    in_elliptic = df.known_elliptic_class.notna()
    anchor = pd.to_datetime(df.anchor_time, utc=True)
    ransom = (df.external_label.fillna("").str.contains("ransomware") & ~in_elliptic
              & (anchor >= pd.to_datetime(df.external_first_seen, errors="coerce", utc=True) - pd.Timedelta(days=30))
              & (anchor <= pd.to_datetime(df.external_last_seen, errors="coerce", utc=True) + pd.Timedelta(days=30)))
    population = df.source.isin(["bq_same_era", "bq_post"]) & ~in_elliptic & df.external_label.isna()
    rows = pd.concat([
        df[df.label.notna()].assign(y=lambda x: x.label.astype(int), w=1.0),
        df[ransom].assign(y=1, w=1.0),
        df[population].sample(POP_ROWS, random_state=0).assign(y=0, w=POP_WEIGHT),
    ])
    early, late = rows[rows.step < HOLDOUT_FROM_STEP], rows[rows.step >= HOLDOUT_FROM_STEP]
    print(f"XGBoost (all labels): {len(rows):,} rows ({int(ransom.sum()):,} ransomware); choosing thresholds ...", flush=True)
    probe = fit_weighted(early, FEATURES)
    late_e = late[late.source == "elliptic_pp"]
    p_e = probe.predict_proba(late_e[FEATURES])[:, 1]
    t_f1 = best_threshold(late_e.y.to_numpy(), p_e)
    late_pop = late[late.w == POP_WEIGHT]
    t_1pct = float(pd.Series(probe.predict_proba(late_pop[FEATURES])[:, 1]).quantile(0.99))
    model = fit_weighted(rows, FEATURES)
    joblib.dump(model, path)
    return {"file": path.name, "threshold": t_f1, "threshold_1pct_random": round(t_1pct, 4),
            "training": "Elliptic++ labels + all Ransomwhere rows + 400k random active addresses (weight 0.5)",
            f"holdout_steps_{HOLDOUT_FROM_STEP}_49": {"PR-AUC": round(average_precision_score(late_e.y, p_e), 4),
                                                    "F1": round(f1_score(late_e.y, p_e >= t_f1), 4)}}


def main() -> None:
    df = pd.read_parquet(DATASET, columns=["step", "label"] + FEATURES)
    labelled = df[df.label.notna()].copy()
    labelled["label"] = labelled.label.astype(int)
    early = labelled[labelled.step < HOLDOUT_FROM_STEP]
    late = labelled[labelled.step >= HOLDOUT_FROM_STEP]

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    card = {"trained_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "features": FEATURES,
            "positive_class": "illicit (Elliptic++ class 1)", "training_rows": len(labelled),
            "training_illicit": int(labelled.label.sum()), "models": {}}
    for name, filename in FILES.items():
        print(f"{name}: choosing threshold on steps {HOLDOUT_FROM_STEP}-49 ...", flush=True)
        builder, params = MODELS[name]
        probe = train(builder, params, early, FEATURES)
        p = probe.predict_proba(late[FEATURES])[:, 1]
        threshold = best_threshold(late.label.to_numpy(), p)
        holdout = {"PR-AUC": round(average_precision_score(late.label, p), 4),
                   "F1": round(f1_score(late.label, p >= threshold), 4)}
        print(f"  threshold {threshold:.2f}, steps {HOLDOUT_FROM_STEP}-49: {holdout}; refitting on all labels ...", flush=True)
        model = train(builder, params, labelled, FEATURES)
        joblib.dump(model, MODELS_DIR / filename)
        card["models"][name] = {"file": filename, "threshold": threshold,
                                f"holdout_steps_{HOLDOUT_FROM_STEP}_49": holdout}
    card["models"]["XGBoost (all labels)"] = train_all_labels(MODELS_DIR / ALL_LABELS_FILE)
    (MODELS_DIR / "model_card.json").write_text(json.dumps(card, indent=2))
    print(f"saved models and model_card.json to {MODELS_DIR}")


if __name__ == "__main__":
    main()
