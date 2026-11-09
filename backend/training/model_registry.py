"""Model registry (Milestone 12) and the recent-data alert threshold (Milestone 9).

Run from backend/ after training.hop_features (about 30-60 minutes; XGBoost uses the GPU if present):
    ..\\.venv\\Scripts\\python.exe -m training.model_registry

What it does
1. Offline benchmark, one fair split for every model (the plan's M10-M11 rules):
   train on Elliptic++ labelled wallets of steps 1-34, pick each model's threshold on steps 31-34
   (best illicit F1, with a model fit on steps 1-30), test once on steps 35-49.
   Models: logistic regression (the baseline), tuned Random Forest and tuned XGBoost, each on
   "v2+exp" (live-compatible wallet features) and "v2+exp+hops" (plus the hop features, which live
   lookups cannot build yet). Random Forest and XGBoost are trained with seeds 0, 1 and 2; a model
   "beats the baseline" only if its mean PR-AUC is above the baseline by more than twice its seed
   spread (standard deviation). The seed-0 model is the one saved and served.
2. Alert threshold on recent data: XGBoost v2+exp+hops trained on every label of steps 1-49, then
   the score that flags 1% of random active addresses (no label) is picked on steps 50-75 and
   checked on steps 76-101, next to the old threshold picked on steps 1-49.
3. Scoring rows: the test rows (labelled, steps 35-49) and the Ransomwhere/OFAC rows from step 35 on,
   with every feature, so the API can score and explain dataset addresses (models/scoring_rows.parquet).

Writes backend/models/ (gitignored): <id>.joblib for every model, registry.json, scoring_rows.parquet,
and reports/model_registry.md.
"""
import hashlib
import json
from datetime import datetime, timezone

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import confusion_matrix
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, StandardScaler
from xgboost import XGBClassifier

from app.ml.transforms import signed_log
from training.build_training_dataset import BACKEND_DIR
from training.compare_models import best_threshold, metrics
from training.hop_check import load
from training.hop_features import HOP_COLS_5
from training.improvement_check import RF_TUNED, V2_EXP, XGB_TUNED
from training.tune_models import fit_retry
from training.xgb_device import xgb_device

MODELS_DIR = BACKEND_DIR / "models"
REGISTRY = MODELS_DIR / "registry.json"
SCORING_ROWS = MODELS_DIR / "scoring_rows.parquet"
REPORT = BACKEND_DIR / "reports" / "model_registry.md"
SEEDS = (0, 1, 2)
FEATURE_SETS = {"v2+exp": V2_EXP, "v2+exp+hops": V2_EXP + HOP_COLS_5}
TRAIN_TO, VALID_FROM, TEST = 34, 31, (35, 49)
FAMILIES = {
    "logistic_regression": "Logistic regression (baseline)",
    "random_forest": "Random Forest (tuned)",
    "xgboost": "XGBoost (tuned)",
}


def build(family: str, y: pd.Series, seed: int):
    if family == "logistic_regression":
        return Pipeline([
            ("impute", SimpleImputer(strategy="median")),
            ("log", FunctionTransformer(signed_log)),
            ("scale", StandardScaler()),
            ("model", LogisticRegression(class_weight="balanced", max_iter=3000, C=1.0)),
        ])
    if family == "random_forest":
        return RandomForestClassifier(**RF_TUNED, class_weight="balanced_subsample", n_jobs=-1, random_state=seed)
    params = dict(XGB_TUNED)
    ratio = float((y == 0).sum() / max((y == 1).sum(), 1)) * params.pop("pos_weight_factor")
    return XGBClassifier(**params, scale_pos_weight=ratio, tree_method="hist", device=xgb_device(), n_jobs=-1,
                         random_state=seed)


def fit(family: str, rows: pd.DataFrame, cols: list[str], seed: int = 0):
    model = fit_retry(lambda: build(family, rows.label, seed), rows[cols], rows.label)
    if family == "xgboost":
        model.set_params(device="cpu")  # serve and explain on the CPU, whatever trained it
    return model


def sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def r4(d: dict) -> dict:
    return {k: round(float(v), 4) for k, v in d.items()}


def benchmark(labelled: pd.DataFrame, version: str) -> list[dict]:
    train = labelled[labelled.step <= TRAIN_TO]
    test = labelled[labelled.step.between(*TEST)]
    probe_rows, valid = train[train.step < VALID_FROM], train[train.step >= VALID_FROM]
    y = test.label.to_numpy()
    entries = []
    for set_name, cols in FEATURE_SETS.items():
        for family, title in FAMILIES.items():
            model_id = f"{family}__{set_name.replace('+', '_')}"
            print(f"{model_id}: threshold on steps {VALID_FROM}-{TRAIN_TO} ...", flush=True)
            probe = fit(family, probe_rows, cols)
            threshold = best_threshold(valid.label.to_numpy(), probe.predict_proba(valid[cols])[:, 1])
            seeds = SEEDS if family != "logistic_regression" else (0,)
            runs, saved = [], None
            for seed in seeds:
                print(f"  seed {seed}: fit on steps 1-{TRAIN_TO}, test on {TEST[0]}-{TEST[1]} ...", flush=True)
                model = fit(family, train, cols, seed)
                p = model.predict_proba(test[cols])[:, 1]
                runs.append(metrics(y, p, threshold))
                if seed == 0:
                    saved, p0 = model, p
            path = MODELS_DIR / f"{model_id}.joblib"
            joblib.dump(saved, path, compress=3)
            tn, fp, fn, tp = confusion_matrix(y, p0 >= threshold).ravel()
            pr = [r["PR-AUC"] for r in runs]
            entries.append({
                "id": model_id, "name": f"{title}, {set_name}", "family": family, "feature_set": set_name,
                "version": f"{version}+{sha256(path)[:8]}", "file": path.name,
                "features": cols, "threshold": round(threshold, 4),
                "threshold_rule": f"best illicit F1 on steps {VALID_FROM}-{TRAIN_TO} (model fit on 1-{VALID_FROM - 1})",
                "metrics": r4(runs[0]),
                "confusion": {"tp": int(tp), "fp": int(fp), "fn": int(fn), "tn": int(tn)},
                "seeds": {"n": len(runs), "pr_auc_mean": round(float(np.mean(pr)), 4),
                          "pr_auc_std": round(float(np.std(pr)), 4),
                          "f1_mean": round(float(np.mean([r["F1"] for r in runs])), 4)},
                "trained_rows": int(len(train)), "trained_illicit": int(train.label.sum()),
                "live_compatible": set_name == "v2+exp",
                "benchmark": "offline",
            })
            print(f"  {entries[-1]['metrics']}", flush=True)
    # Beats the logistic-regression baseline of the same feature set by more than twice its seed spread?
    for e in entries:
        base = next(b for b in entries if b["family"] == "logistic_regression" and b["feature_set"] == e["feature_set"])
        margin = e["seeds"]["pr_auc_mean"] - base["seeds"]["pr_auc_mean"]
        e["beats_baseline"] = None if e is base else bool(margin > 2 * e["seeds"]["pr_auc_std"])
        e["pr_auc_vs_baseline"] = round(margin, 4)
    return entries


def alert_threshold(df: pd.DataFrame, labelled: pd.DataFrame, version: str) -> dict:
    """XGBoost v2+exp+hops on every label of steps 1-49; 1%-of-random thresholds old and recent."""
    cols = FEATURE_SETS["v2+exp+hops"]
    print("alert model: XGBoost v2+exp+hops on all labels of steps 1-49 ...", flush=True)
    model = fit("xgboost", labelled, cols)
    path = MODELS_DIR / "xgboost__alert_hops.joblib"
    joblib.dump(model, path, compress=3)
    random_rows = df[df.source.isin(["bq_same_era", "bq_post"]) & df.known_elliptic_class.isna() & df.external_label.isna()]
    bands = {"1-49": (1, 49), "50-75": (50, 75), "76-101": (76, 101)}
    scores = {}
    for band, (lo, hi) in bands.items():
        rows = random_rows[random_rows.step.between(lo, hi)]
        rows = rows.sample(min(len(rows), 150_000), random_state=0)
        scores[band] = model.predict_proba(rows[cols])[:, 1]
    ransom = df[(df.external_label == "ransomware") & (df.step >= 50)]
    ransom_p = model.predict_proba(ransom[cols])[:, 1] if len(ransom) else np.array([])
    old = float(np.quantile(scores["1-49"], 0.99))
    new = float(np.quantile(scores["50-75"], 0.99))
    flagged = lambda band, t: round(float((scores[band] >= t).mean()), 4)
    caught = lambda t: round(float((ransom_p >= t).mean()), 4) if len(ransom_p) else None
    return {
        "model_id": "xgboost__alert_hops", "file": path.name, "version": f"{version}+{sha256(path)[:8]}",
        "features": cols, "trained_rows": int(len(labelled)),
        "old_threshold": round(old, 4), "old_rule": "flags 1% of random active addresses in steps 1-49",
        "recent_threshold": round(new, 4), "recent_rule": "flags 1% of random active addresses in steps 50-75",
        "random_rows_scored": {b: int(len(s)) for b, s in scores.items()},
        "random_flagged": {
            "old_threshold": {b: flagged(b, old) for b in bands},
            "recent_threshold": {b: flagged(b, new) for b in bands},
        },
        "ransomware_rows_steps_50_101": int(len(ransom_p)),
        "ransomware_flagged": {"old_threshold": caught(old), "recent_threshold": caught(new)},
        "score_quantiles": {b: {q: round(float(np.quantile(s, q)), 4) for q in (0.5, 0.9, 0.99, 0.999)}
                            for b, s in scores.items()},
    }


def scoring_rows(df: pd.DataFrame) -> int:
    """Rows the API can score: test rows (steps 35-49, labelled) and listed rows from step 35 on."""
    keep = (df.label.notna() & df.step.between(*TEST)) | (df.external_label.notna() & (df.step >= TEST[0]))
    cols = ["address", "step", "source", "label", "external_label"] + list(dict.fromkeys(FEATURE_SETS["v2+exp+hops"]))
    rows = df.loc[keep, cols].copy()
    for c in rows.columns:
        if rows[c].dtype == "float64":
            rows[c] = rows[c].astype("float32")
    rows.to_parquet(SCORING_ROWS, index=False)
    return len(rows)


def write_report(registry: dict) -> None:
    models = registry["models"]
    alert = registry["alert_threshold"]
    lines = [
        "# Model registry and offline benchmark",
        "",
        f"Generated {registry['generated_utc']} by training/model_registry.py. {registry['benchmark']}.",
        f"Test rows: {registry['test_rows']:,} ({registry['test_illicit']:,} illicit).",
        "",
        "## Comparison (seed 0 model; threshold chosen on validation steps 31-34)",
        "",
        "| Model | Features | PR-AUC | ROC-AUC | Precision | Recall | F1 | PR-AUC mean ± sd (seeds) | Beats baseline |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        *[f"| {FAMILIES[m['family']]} | {m['feature_set']} | {m['metrics']['PR-AUC']:.3f} | {m['metrics']['ROC-AUC']:.3f} | "
          f"{m['metrics']['precision']:.3f} | {m['metrics']['recall']:.3f} | {m['metrics']['F1']:.3f} | "
          f"{m['seeds']['pr_auc_mean']:.3f} ± {m['seeds']['pr_auc_std']:.3f} ({m['seeds']['n']}) | "
          f"{'baseline' if m['beats_baseline'] is None else ('yes' if m['beats_baseline'] else 'no')} |" for m in models],
        "",
        "Confusion matrices at each model's threshold (test steps 35-49):",
        "",
        "| Model | Features | Threshold | TP | FP | FN | TN |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        *[f"| {FAMILIES[m['family']]} | {m['feature_set']} | {m['threshold']:.2f} | {m['confusion']['tp']:,} | "
          f"{m['confusion']['fp']:,} | {m['confusion']['fn']:,} | {m['confusion']['tn']:,} |" for m in models],
        "",
        "## Alert threshold re-picked on recent data (steps 50-101)",
        "",
        f"Model: XGBoost v2+exp+hops trained on all {alert['trained_rows']:,} labelled rows of steps 1-49. There are no",
        "Elliptic++ labels after step 49, so the threshold is the score that flags 1% of random active addresses",
        "(presumed mostly licit), picked on steps 50-75 and checked on steps 76-101.",
        "",
        "| Threshold | Value | Random flagged, steps 1-49 | Steps 50-75 | Steps 76-101 | Ransomware rows flagged, steps 50-101 |",
        "| --- | --- | --- | --- | --- | --- |",
        *[f"| {label} | {alert[key]:.4f} | {alert['random_flagged'][key]['1-49']:.2%} | {alert['random_flagged'][key]['50-75']:.2%} | "
          f"{alert['random_flagged'][key]['76-101']:.2%} | "
          f"{'n/a' if alert['ransomware_flagged'][key] is None else format(alert['ransomware_flagged'][key], '.1%')} |"
          for label, key in (("Old (steps 1-49)", "old_threshold"), ("Recent (steps 50-75)", "recent_threshold"))],
        "",
        f"Ransomware rows after step 49: {alert['ransomware_rows_steps_50_101']:,}.",
        "",
        "## Rebuild",
        "",
        "```",
        "cd backend",
        "..\\.venv\\Scripts\\python.exe -m training.model_registry",
        "```",
        "",
    ]
    REPORT.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    version = now.strftime("%Y.%m.%d")
    df = load()
    labelled = df[df.label.notna()].copy()
    labelled["label"] = labelled.label.astype(int)
    test = labelled[labelled.step.between(*TEST)]
    models = benchmark(labelled, version)
    registry = {
        "generated_utc": now.isoformat(timespec="seconds"),
        "benchmark": ("Offline benchmark on Elliptic++ wallets: train steps 1-34, threshold on steps 31-34, "
                      "test steps 35-49 (31 Mar to 2 Oct 2017)"),
        "positive_class": "illicit (Elliptic++ class 1)",
        "test_rows": int(len(test)), "test_illicit": int(test.label.sum()),
        "models": models,
    }
    registry["alert_threshold"] = alert_threshold(df, labelled, version)
    registry["scoring_rows"] = scoring_rows(df)
    REGISTRY.write_text(json.dumps(registry, indent=2))
    write_report(registry)
    print(f"saved {len(models) + 1} models, {REGISTRY.name} and {SCORING_ROWS.name} to {MODELS_DIR}; report {REPORT}")


if __name__ == "__main__":
    main()
