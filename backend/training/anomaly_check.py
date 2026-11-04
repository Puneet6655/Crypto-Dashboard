"""Anomaly test: does an Isolation Forest find illicit wallets, and does it help XGBoost?

Run from backend/ (the offline part takes about 5-10 minutes; XGBoost uses the GPU if present):
    ..\\.venv\\Scripts\\python.exe -m training.anomaly_check offline
    ..\\.venv\\Scripts\\python.exe -m training.anomaly_check recent   # after training.score_recent
    ..\\.venv\\Scripts\\python.exe -m training.anomaly_check report   # rewrite the report from saved results

Offline: same data and split as training/model_registry.py (training.hop_check.load): Elliptic++
labelled wallets, train steps 1-34, test steps 35-49, live-compatible v2+exp features.
  * Isolation Forest (sklearn) fit on the train rows WITHOUT their labels, on signed_log features
    (heavy-tailed BTC amounts otherwise dominate the random splits). Anomaly score = -score_samples.
  * A variant fit on licit train rows only ("what normal looks like").
  * Tuned XGBoost (the registry settings) alone, with the anomaly score as an extra feature, and a
    rank-average blend of XGBoost and the anomaly score. Three seeds each.
  * The top 1% most anomalous test rows: how many are illicit and licit.
Recent: scores the recent batch (data/processed/recent_scores/) with the saved forest and lists the
most unusual random recent addresses that the alert model does NOT flag.
Writes reports/anomaly_check.md, data/processed/anomaly/*.json, models/isolation_forest__v2_exp.joblib.
"""
import argparse
import json
from datetime import datetime, timezone

import joblib
import numpy as np
import pandas as pd
from scipy.stats import rankdata
from sklearn.ensemble import IsolationForest
from sklearn.metrics import average_precision_score, roc_auc_score

from app.ml.transforms import signed_log
from training.bq_common import BACKEND_DIR
from training.improvement_check import V2_EXP, XGB_TUNED
from training.tune_models import fit_retry
from training.xgb_device import xgb_device

OUT = BACKEND_DIR / "data" / "processed" / "anomaly"
MODEL = BACKEND_DIR / "models" / "isolation_forest__v2_exp.joblib"
REPORT = BACKEND_DIR / "reports" / "anomaly_check.md"
RECENT = BACKEND_DIR / "data" / "processed" / "recent_scores"
SEEDS = (0, 1, 2)
IF_PARAMS = {"n_estimators": 300, "max_samples": 1024, "n_jobs": -1}


def forest(X: pd.DataFrame, seed: int) -> IsolationForest:
    return IsolationForest(**IF_PARAMS, random_state=seed).fit(signed_log(X.to_numpy(np.float64)))


def anomaly(model: IsolationForest, X: pd.DataFrame) -> np.ndarray:
    """Higher = more unusual."""
    return -model.score_samples(signed_log(X.to_numpy(np.float64)))


def xgb(y: pd.Series, seed: int):
    from xgboost import XGBClassifier

    params = dict(XGB_TUNED)
    ratio = float((y == 0).sum() / max((y == 1).sum(), 1)) * params.pop("pos_weight_factor")
    return XGBClassifier(**params, scale_pos_weight=ratio, tree_method="hist", device=xgb_device(), n_jobs=-1,
                         random_state=seed)


def rank01(x: np.ndarray) -> np.ndarray:
    return (rankdata(x) - 1) / max(len(x) - 1, 1)


def pr(y, p) -> float:
    return round(float(average_precision_score(y, p)), 4)


def offline() -> dict:
    from training.hop_check import load

    df = load()
    labelled = df[df.label.notna()].copy()
    labelled["label"] = labelled.label.astype(int)
    train, test = labelled[labelled.step <= 34], labelled[labelled.step.between(35, 49)]
    y = test.label.to_numpy()
    print(f"train {len(train):,} rows, test {len(test):,} ({y.sum():,} illicit)", flush=True)

    runs = []
    for seed in SEEDS:
        print(f"seed {seed}: isolation forests ...", flush=True)
        f_all = forest(train[V2_EXP], seed)
        f_licit = forest(train.loc[train.label == 0, V2_EXP], seed)
        a_train, a_test = anomaly(f_all, train[V2_EXP]), anomaly(f_all, test[V2_EXP])
        a_licit_test = anomaly(f_licit, test[V2_EXP])
        if seed == 0:
            MODEL.parent.mkdir(parents=True, exist_ok=True)
            joblib.dump({"model": f_all, "features": V2_EXP, "transform": "signed_log",
                         "params": IF_PARAMS, "fit_rows": int(len(train)),
                         "test_anomaly_quantiles": {q: float(np.quantile(a_test, q)) for q in (0.5, 0.9, 0.99)}},
                        MODEL, compress=3)
            top = a_test >= np.quantile(a_test, 0.99)
            top_counts = {"rows": int(top.sum()), "illicit": int(y[top].sum()), "licit": int((1 - y[top]).sum()),
                          "illicit_share": round(float(y[top].mean()), 4), "base_rate": round(float(y.mean()), 4)}
            top_licit = a_licit_test >= np.quantile(a_licit_test, 0.99)
            top_counts_licit = {"rows": int(top_licit.sum()), "illicit": int(y[top_licit].sum()),
                                "licit": int((1 - y[top_licit]).sum())}
        print(f"seed {seed}: XGBoost alone and with the anomaly feature ...", flush=True)
        base = fit_retry(lambda: xgb(train.label, seed), train[V2_EXP], train.label)
        p_base = base.predict_proba(test[V2_EXP])[:, 1]
        with_feat = fit_retry(lambda: xgb(train.label, seed), train[V2_EXP].assign(anomaly=a_train), train.label)
        p_feat = with_feat.predict_proba(test[V2_EXP].assign(anomaly=a_test))[:, 1]
        blends = {w: (1 - w) * rank01(p_base) + w * rank01(a_test) for w in (0.1, 0.25, 0.5)}
        runs.append({
            "seed": seed,
            "anomaly_alone": {"PR-AUC": pr(y, a_test), "ROC-AUC": round(float(roc_auc_score(y, a_test)), 4)},
            "anomaly_licit_fit": {"PR-AUC": pr(y, a_licit_test),
                                  "ROC-AUC": round(float(roc_auc_score(y, a_licit_test)), 4)},
            "xgboost": {"PR-AUC": pr(y, p_base), "ROC-AUC": round(float(roc_auc_score(y, p_base)), 4)},
            "xgboost_plus_anomaly_feature": {"PR-AUC": pr(y, p_feat),
                                             "ROC-AUC": round(float(roc_auc_score(y, p_feat)), 4)},
            "xgboost_rank_blend": {str(w): {"PR-AUC": pr(y, b)} for w, b in blends.items()},
            "xgboost_rank_only": {"PR-AUC": pr(y, rank01(p_base))},
        })
        print(json.dumps(runs[-1]), flush=True)

    def summary(path):
        vals = [r for r in runs]
        for key in path:
            vals = [v[key] for v in vals]
        return {"mean": round(float(np.mean(vals)), 4), "sd": round(float(np.std(vals)), 4)}

    result = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "train_rows": int(len(train)), "test_rows": int(len(test)), "test_illicit": int(y.sum()),
        "features": "v2+exp (89, signed_log for the forest)", "forest": IF_PARAMS, "runs": runs,
        "mean": {
            "anomaly_alone": summary(["anomaly_alone", "PR-AUC"]),
            "anomaly_licit_fit": summary(["anomaly_licit_fit", "PR-AUC"]),
            "xgboost": summary(["xgboost", "PR-AUC"]),
            "xgboost_plus_anomaly_feature": summary(["xgboost_plus_anomaly_feature", "PR-AUC"]),
            **{f"blend_{w}": summary(["xgboost_rank_blend", str(w), "PR-AUC"]) for w in (0.1, 0.25, 0.5)},
            "xgboost_rank_only": summary(["xgboost_rank_only", "PR-AUC"]),
        },
        "top_1pct_anomalous_test": top_counts, "top_1pct_anomalous_test_licit_fit": top_counts_licit,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "offline.json").write_text(json.dumps(result, indent=2))
    return result


def recent(top_n: int = 200) -> dict:
    """Anomaly scores for the recent batch; the most unusual random addresses the alert model does not flag."""
    saved = joblib.load(MODEL)
    features = pd.read_parquet(RECENT / "features.parquet")
    scores = pd.read_parquet(RECENT / "scores.parquet")
    summary = json.loads((RECENT / "summary.json").read_text())
    threshold = summary["thresholds"]["xgboost__alert_hops"]["new"]
    df = scores.merge(features[["address"] + saved["features"]], on="address", validate="one_to_one")
    df["anomaly"] = anomaly(saved["model"], df[saved["features"]])
    q99 = float(np.quantile(df.loc[df.in_random, "anomaly"], 0.99))
    df["anomaly_top1pct"] = df.anomaly >= q99
    unusual = df[df.in_random & df.anomaly_top1pct & (df.score_alert < threshold)].sort_values("anomaly", ascending=False)
    keep = ["address", "anomaly", "score_alert", "score_v2_exp", "listed_sources", "anchor_time", "total_txs",
            "btc_received_total", "btc_sent_total", "num_counterparties", "tx_outputs_max", "tx_inputs_max",
            "fee_rate_median", "window_truncated"]
    keep = [c for c in keep if c in df.columns or c in features.columns]
    extra = [c for c in keep if c not in unusual.columns]
    if extra:
        unusual = unusual.merge(features[["address"] + extra], on="address", how="left")
    unusual[keep].to_parquet(RECENT / "unusual_not_flagged.parquet", index=False)
    df[["address", "anomaly", "anomaly_top1pct"]].to_parquet(RECENT / "anomaly_scores.parquet", index=False)
    rand = df[df.in_random]
    flagged = rand.score_alert >= threshold
    listed = df[df.listed_sources.notna()]
    result = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rows": int(len(df)), "random_rows": int(len(rand)), "alert_threshold_new": threshold,
        "anomaly_q99_random": round(q99, 4),
        "random_top1pct": int(rand.anomaly_top1pct.sum()),
        "random_top1pct_also_flagged": int((rand.anomaly_top1pct & flagged).sum()),
        "unusual_not_flagged": int(len(unusual)),
        "spearman_anomaly_vs_alert_random": round(float(rand.anomaly.rank().corr(rand.score_alert.rank())), 4),
        "listed_rows": int(len(listed)), "listed_top1pct_anomalous": int(listed.anomaly_top1pct.sum()),
        "examples": unusual[keep].head(15).astype({"anchor_time": str}).to_dict(orient="records") if len(unusual) else [],
    }
    (OUT / "recent.json").write_text(json.dumps(result, indent=2, default=str))
    return result


def report() -> None:
    off = json.loads((OUT / "offline.json").read_text()) if (OUT / "offline.json").exists() else None
    rec = json.loads((OUT / "recent.json").read_text()) if (OUT / "recent.json").exists() else None
    lines = ["# Anomaly check: Isolation Forest on v2+exp features", ""]
    if off:
        m = off["mean"]
        f = lambda k: f"{m[k]['mean']:.3f} ± {m[k]['sd']:.3f}"  # noqa: E731
        t = off["top_1pct_anomalous_test"]
        tl = off["top_1pct_anomalous_test_licit_fit"]
        base = m["xgboost"]["mean"]
        best_blend = max((k for k in m if k.startswith("blend_")), key=lambda k: m[k]["mean"])
        lines += [
            f"Generated {off['generated_utc']} by training/anomaly_check.py. Same data and split as the model "
            f"registry: Elliptic++ labelled wallets, train steps 1-34 ({off['train_rows']:,} rows), test steps 35-49 "
            f"({off['test_rows']:,} rows, {off['test_illicit']:,} illicit, base rate {t['base_rate']:.1%}). "
            f"Features: {off['features']}. Forest: {off['forest']['n_estimators']} trees, "
            f"{off['forest']['max_samples']} samples per tree. Three seeds; mean ± sd.",
            "",
            "| Scorer | Test PR-AUC |",
            "| --- | --- |",
            f"| Anomaly score alone (forest fit on all train rows, labels unused) | {f('anomaly_alone')} |",
            f"| Anomaly score alone (forest fit on licit train rows only) | {f('anomaly_licit_fit')} |",
            f"| Tuned XGBoost alone (v2+exp) | {f('xgboost')} |",
            f"| XGBoost + anomaly score as a feature | {f('xgboost_plus_anomaly_feature')} |",
            f"| Rank blend 90% XGBoost / 10% anomaly | {f('blend_0.1')} |",
            f"| Rank blend 75% / 25% | {f('blend_0.25')} |",
            f"| Rank blend 50% / 50% | {f('blend_0.5')} |",
            f"| XGBoost ranks only (blend reference: ranking changes PR-AUC ties slightly) | {f('xgboost_rank_only')} |",
            "",
            f"Top 1% most anomalous test rows (seed 0): {t['rows']:,} rows, {t['illicit']:,} illicit and "
            f"{t['licit']:,} licit ({t['illicit_share']:.1%} illicit against a {t['base_rate']:.1%} base rate). "
            f"With the licit-only forest: {tl['illicit']:,} illicit, {tl['licit']:,} licit.",
            "",
            "## What this means",
            "",
            f"- The anomaly score alone is a weak illicit detector (PR-AUC {m['anomaly_alone']['mean']:.3f} against a "
            f"{t['base_rate']:.3f} base rate, XGBoost {base:.3f}): unusual is not the same as illicit.",
            f"- It even points the wrong way: ROC-AUC {np.mean([r['anomaly_alone']['ROC-AUC'] for r in off['runs']]):.2f} "
            "(below 0.5), and the top 1% most unusual rows are mostly licit: in these features Elliptic++ illicit "
            "wallets look more typical than licit ones.",
            f"- As an extra XGBoost feature it changes PR-AUC by {m['xgboost_plus_anomaly_feature']['mean'] - base:+.3f}; "
            f"the best rank blend ({best_blend.replace('blend_', 'weight ')}) changes it by "
            f"{m[best_blend]['mean'] - base:+.3f}. A change smaller than about twice the seed spread "
            f"({2 * m['xgboost']['sd']:.3f}) is noise.",
            "- Use it as a separate review list (\"unusual, not flagged\"), not as part of the alert score.",
            "",
        ]
    if rec:
        lines += [
            "## Recent batch (2026)",
            "",
            f"The saved forest (seed 0) scored {rec['rows']:,} recent addresses ({rec['random_rows']:,} random). "
            f"{rec['random_top1pct']:,} random rows are in the top 1% most unusual (anomaly >= {rec['anomaly_q99_random']:.3f}); "
            f"{rec['random_top1pct_also_flagged']:,} of them are also flagged by the alert model at the new threshold "
            f"{rec['alert_threshold_new']:.4f}, so {rec['unusual_not_flagged']:,} are \"unusual but not flagged\" "
            f"(saved to data/processed/recent_scores/unusual_not_flagged.parquet). Rank correlation of anomaly and alert "
            f"score on random rows: {rec['spearman_anomaly_vs_alert_random']:.2f}. Known-listed recent addresses in the top "
            f"1%: {rec['listed_top1pct_anomalous']:,} of {rec['listed_rows']:,}.",
            "",
            "Most unusual random addresses the alert model does not flag (first 15):",
            "",
            "| Address | Anomaly | Alert score | Txs in window | Max outputs | BTC received |",
            "| --- | --- | --- | --- | --- | --- |",
            *[f"| `{r['address']}` | {r['anomaly']:.3f} | {r['score_alert']:.3f} | {r.get('total_txs', 0):.0f} | "
              f"{r.get('tx_outputs_max', 0):.0f} | {r.get('btc_received_total', 0):.3f} |" for r in rec["examples"]],
            "",
            "These are leads for a human to look at, not accusations: big batch payers, consolidations and new "
            "script types all look unusual against 2016-2017 wallets.",
            "",
        ]
    lines += ["## Rebuild", "", "```", "cd backend", "..\\.venv\\Scripts\\python.exe -m training.anomaly_check offline",
              "..\\.venv\\Scripts\\python.exe -m training.anomaly_check recent", "```", ""]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {REPORT}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=["offline", "recent", "report"])
    args = parser.parse_args()
    if args.mode == "offline":
        offline()
    elif args.mode == "recent":
        print(json.dumps(recent(), indent=2, default=str)[:3000])
    report()


if __name__ == "__main__":
    main()
