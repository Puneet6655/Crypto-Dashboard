"""Do multi-hop graph features help? Tuned XGBoost on v2+exp, + hops 1-2, + hops 1-5.

Run from backend/ after training.hop_features (about 30-60 minutes):
    python -m training.hop_check

Same split and model as training/improvement_check.py: Elliptic++ labelled rows, train steps 1-34
(threshold from steps 31-34 with a model fit on 1-30), test steps 35-49. Checks:
  1. trained once; 2. retrained before each test step; 3. address-disjoint test (test addresses never
  labelled in steps 1-34); 4. strict threshold = the score that flags 1% of random active addresses
  (not in Elliptic++, no external label) in steps 31-34; 5. share of rows at each hop distance.
Writes reports/hop_features.md and data/processed/hop_features/eval_summary.json.
"""
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, f1_score, precision_score, recall_score, roc_auc_score

from training.bq_common import COST_LOG
from training.build_training_dataset import BACKEND_DIR, DATASET
from training.compare_models import metrics
from training.hop_features import HOP_COLS_2, HOP_COLS_5, HUB_COUNTERPARTIES, OUT as HOP_DIR
from training.improvement_check import MODELS, V2_EXP, train, train_with_threshold
from training.transaction_store import MANIFEST, STORE

REPORT = BACKEND_DIR / "reports" / "hop_features.md"
SETS = {"v2+exp": V2_EXP, "+ hops 1-2": V2_EXP + HOP_COLS_2, "+ hops 1-5": V2_EXP + HOP_COLS_5}
BUILDER, PARAMS = MODELS["XGBoost (tuned)"]
# Conclusions from the 2026-10-31 run; update them if a rerun changes the numbers.
CONCLUSIONS = """- Hop features help clearly. Trained once, PR-AUC rises from 0.663 (v2+exp) to 0.735 with hops 1-2 and 0.761
  with hops 1-5; retrained before each step, from 0.753 to 0.810 and 0.833. F1 rises from 0.605 to 0.659
  (0.677 to 0.750 retrained). The gain holds on the address-disjoint test (0.665 to 0.760), so it is not the
  model remembering addresses it saw in training.
- Hops 3-5 add a smaller but real gain on top of hops 1-2: +0.026 PR-AUC trained once, +0.023 retrained, and
  higher precision at the same threshold. Most of the lift comes from 1-2 hops.
- The strongest hop features are the BTC share exchanged directly with known-illicit addresses and the 2-hop
  neighbourhood size (how busy the counterparties' counterparties are). Hop distance itself matters less
  than how much value moved and how dense the neighbourhood is.
- Hop distance separates the classes: 42% of illicit test rows are within 2 hops of an earlier illicit label,
  against 29% of licit rows and 9% of random active addresses; 57% of licit rows are 1 hop from a known
  licit address, against 10% of illicit rows.
- At the strict threshold (1% of random addresses in steps 31-34) the model flags 0.4% of random addresses in
  steps 35-49 with 97-98% precision, but recall stays near 28-29%: hop features make the top of the list
  cleaner without catching many more illicit rows there. At the best-F1 threshold they flag more random
  addresses (5.4% to 6.6-8.0%), so use the strict threshold for live alerts.
- Caveats: Elliptic++ itself was sampled around labelled transactions, so part of the gain may reflect how
  that sample was drawn; random-address checks are the best guard. The graph only contains transactions of
  addresses active in each snapshot (and their 100 newest transactions), so live lookups need the same
  neighbourhood (each counterparty's newest transactions) to reproduce these features. Ransomware families
  and OFAC were not re-tested here."""


def load() -> pd.DataFrame:
    cols = ["address", "step", "source", "label", "known_elliptic_class", "external_label", "anchor_time"] + V2_EXP
    df = pd.read_parquet(DATASET, columns=list(dict.fromkeys(cols)))
    df["address"] = df["address"].astype(str)
    hops = pd.concat([pd.read_parquet(p) for p in sorted(HOP_DIR.glob("step=*.parquet"))], ignore_index=True)
    hops = hops.drop(columns=["illicit_nearest_seed", "illicit_path_txs"])
    df = df.merge(hops, on=["address", "step"], how="inner", validate="one_to_one")
    absent = sorted(set(range(1, 102)) - set(hops.step.unique()))
    if absent:
        print(f"no hop features yet for steps {absent[0]}-{absent[-1]} ({len(absent)} steps); left out", flush=True)
    missing = df[HOP_COLS_5[0]].isna().sum()
    if missing:
        raise RuntimeError(f"{missing:,} rows have no hop features")
    return df


def at(y, p, t) -> dict:
    pred = p >= t
    return {"precision": precision_score(y, pred, zero_division=0), "recall": recall_score(y, pred),
            "F1": f1_score(y, pred)}


def fmt(r: dict) -> str:
    return " | ".join(f"{r[k]:.3f}" for k in ("PR-AUC", "ROC-AUC", "precision", "recall", "F1"))


def main() -> None:
    import sys
    reuse = "--reuse" in sys.argv
    df = load()
    labelled = df[df.label.notna()].copy()
    labelled["label"] = labelled.label.astype(int)
    tr, te = labelled[labelled.step <= 34], labelled[labelled.step.between(35, 49)]
    y = te.label.to_numpy()
    random_rows = df[(df.source == "bq_same_era") & df.known_elliptic_class.isna() & df.external_label.isna()]
    rand_cal = random_rows[random_rows.step.between(31, 34)]
    rand_test = random_rows[random_rows.step.between(35, 49)]
    seen = set(tr.address)
    disjoint = ~te.address.isin(seen).to_numpy()

    results, importances = {}, {}
    summary_path = HOP_DIR / "eval_summary.json"
    if reuse:
        saved = json.loads(summary_path.read_text())
        results = saved["results"]
        importances = {k: pd.Series(v) for k, v in saved["importances"].items()}
    for name, cols in ({} if reuse else SETS).items():
        print(f"{name}: trained once ...", flush=True)
        clf, t = train_with_threshold(BUILDER, PARAMS, tr, cols)
        p = clf.predict_proba(te[cols])[:, 1]
        strict = float(np.quantile(clf.predict_proba(rand_cal[cols])[:, 1], 0.99))
        r = {"static": metrics(y, p, t), "threshold": t, "strict_threshold": strict,
             "disjoint": metrics(y[disjoint], p[disjoint], t),
             "strict": {**at(y, p, strict), "random_flagged_35_49": float((clf.predict_proba(rand_test[cols])[:, 1] >= strict).mean())},
             "best_f1_random_flagged_35_49": float((clf.predict_proba(rand_test[cols])[:, 1] >= t).mean())}
        gain = pd.Series(clf.get_booster().get_score(importance_type="gain"))
        importances[name] = (gain / gain.sum()).sort_values(ascending=False)
        if name == "+ hops 1-5":
            importances[name].to_csv(HOP_DIR / "gain_hops_1_5.csv")
        print(f"{name}: retraining before each step ...", flush=True)
        rolling = np.zeros(len(te))
        for step in range(35, 50):
            model = train(BUILDER, PARAMS, labelled[labelled.step < step], cols)
            mask = (te.step == step).to_numpy()
            rolling[mask] = model.predict_proba(te.loc[mask, cols])[:, 1]
        r["rolling"] = {"PR-AUC": average_precision_score(y, rolling), "ROC-AUC": roc_auc_score(y, rolling),
                        **at(y, rolling, t)}
        results[name] = r
        print(json.dumps({k: v for k, v in r.items()}, default=float), flush=True)

    # Hop distance shares (1-5, 6 = none), for test rows by class and random test-step rows.
    groups = {"Illicit (test steps 35-49)": te[te.label == 1], "Licit (test steps 35-49)": te[te.label == 0],
              "Random active addresses (steps 35-49)": rand_test, "All rows, steps 50-101": df[df.step >= 50]}
    groups = {g: rows for g, rows in groups.items() if len(rows)}
    dist = {g: rows.illicit_hop_dist.value_counts(normalize=True).reindex(range(1, 7), fill_value=0)
            for g, rows in groups.items()}
    ldist = {g: rows.licit_hop_dist.value_counts(normalize=True).reindex(range(1, 7), fill_value=0)
             for g, rows in groups.items()}
    stats = pd.DataFrame([json.loads(p.read_text()) for p in sorted(HOP_DIR.glob("step=*.json"))])
    manifest = json.loads(MANIFEST.read_text())
    store_rows = sum(v["rows"] for v in manifest.values())
    store_bytes = sum(p.stat().st_size for p in STORE.glob("step=*/*.parquet"))
    log = pd.read_csv(COST_LOG)
    billed = log[log.label.str.startswith(("window_transactions", "active_addresses_v2_copy"))].billed_gb.sum()
    write_report(results, importances, dist, ldist, stats, store_rows, store_bytes, billed, len(te), int(disjoint.sum()))
    summary = {"results": results, "store_rows": store_rows, "store_bytes": store_bytes, "billed_gb": billed,
               "importances": {k: v.to_dict() for k, v in importances.items()}}
    summary_path.write_text(json.dumps(summary, indent=2, default=float))


def alert_section() -> list[str]:
    """Summary of data/processed/alert_queue/ (built by training/alert_queue.py), if it exists."""
    folder = BACKEND_DIR / "data" / "processed" / "alert_queue"
    if not (folder / "addresses.parquet").exists():
        return []
    a = pd.read_parquet(folder / "addresses.parquet")
    t = pq_rows(folder / "transactions.parquet")
    near = a[a.reason == "near_illicit"]
    known = a[a.reason == "known_illicit"]
    return [
        "## Alert queue data",
        "",
        f"Built by training/alert_queue.py with the {a.model_features.iloc[0]} model trained on all Elliptic++ labels "
        f"(steps 1-49); strict threshold {a.threshold.iloc[0]:.4f} (flags 1% of random active addresses in steps 1-49).",
        "",
        f"- data/processed/alert_queue/addresses.parquet: {len(a):,} rows (one per address and step),"
        f" {a.address.nunique():,} distinct addresses, {(folder / 'addresses.parquet').stat().st_size / 1e6:.1f} MB.",
        f"  - known_illicit: {len(known):,} rows ({(known.nearest_seed_source == 'elliptic_pp').sum():,} Elliptic++,"
        f" {(known.nearest_seed_source == 'ransomwhere').sum():,} Ransomwhere): labelled illicit before the step and active in it.",
        f"  - near_illicit: {len(near):,} rows within 2 hops of a known-illicit address with score >= threshold"
        f" ({(near.hop_distance == 1).sum():,} at 1 hop, {(near.hop_distance == 2).sum():,} at 2 hops;"
        f" {(near.step <= 49).sum():,} in steps 1-49, {(near.step > 49).sum():,} in steps 50-101).",
        f"    {int((near.elliptic_label == 1).sum()):,} of them are Elliptic++ illicit rows and {int((near.elliptic_label == 0).sum()):,} licit;"
        " the rest are unlabelled.",
        "  - Columns: alert_id, address, step, snap_end_time, anchor_time, anchor_block, reason, score, threshold,",
        "    above_threshold, hop_distance (0 for known), nearest_seed, nearest_seed_source, evidence_tx_hashes (path",
        "    from the address to the seed), illicit_within_1/_2, illicit_btc_share_1hop, licit_hop_dist, elliptic_label,",
        "    external_family, score_in_training (true for labelled steps 1-49 rows the model was fit on), model_features.",
        f"- data/processed/alert_queue/transactions.parquet: {t:,} transactions"
        f" ({(folder / 'transactions.parquet').stat().st_size / 1e6:.0f} MB): every saved transaction of the step that",
        "  touches an alert address, plus every evidence transaction. Columns: step, tx_hash, block_height, block_time,",
        "  is_coinbase, fee_sats, total_input_btc, total_output_btc, inputs / outputs (list of {address, value} in sats),",
        "  alert_addresses, is_evidence.",
        "- Few near_illicit rows after step 49: the threshold comes from 2016-17 addresses and scores drift lower",
        "  in 2018-19. Re-pick the threshold on recent random addresses before live use.",
        "",
    ]


def pq_rows(path) -> int:
    import pyarrow.parquet as pq
    return pq.ParquetFile(path).metadata.num_rows


def write_report(results, importances, dist, ldist, stats, store_rows, store_bytes, billed, n_test, n_disjoint) -> None:
    base, h2, h5 = (results[k] for k in SETS)
    rows = lambda key: [f"| {name} | {fmt(r[key])} |" for name, r in results.items()]
    gain5 = importances["+ hops 1-5"]
    hop_gain = gain5[gain5.index.isin(HOP_COLS_5)]
    lines = [
        "# Multi-hop graph features",
        "",
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by training/hop_check.py (features: training/hop_features.py,",
        "transactions: training/transaction_store.py). Model: tuned XGBoost (reports/tuning.md). Split as in",
        "reports/improvement_check.md: train steps 1-34 (threshold from steps 31-34), test steps 35-49 "
        f"({n_test:,} labelled rows).",
        "",
        "## Results",
        "",
        "### 1. Trained once on steps 1-34 (best-F1 threshold)",
        "",
        "| Features | PR-AUC | ROC-AUC | Precision | Recall | F1 |",
        "| --- | --- | --- | --- | --- | --- |",
        *rows("static"),
        "",
        "### 2. Retrained before each test step (precision/recall/F1 at the trained-once threshold)",
        "",
        "| Features | PR-AUC | ROC-AUC | Precision | Recall | F1 |",
        "| --- | --- | --- | --- | --- | --- |",
        *rows("rolling"),
        "",
        f"### 3. Address-disjoint test ({n_disjoint:,} test rows whose address has no labelled row in steps 1-34)",
        "",
        "| Features | PR-AUC | ROC-AUC | Precision | Recall | F1 |",
        "| --- | --- | --- | --- | --- | --- |",
        *rows("disjoint"),
        "",
        "### 4. Strict threshold (flags 1% of random active addresses in steps 31-34)",
        "",
        "| Features | Threshold | Precision | Recall | F1 | Random flagged, steps 35-49 | Random flagged at best-F1 threshold |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        *[f"| {n} | {r['strict_threshold']:.3f} | {r['strict']['precision']:.3f} | {r['strict']['recall']:.3f} | "
          f"{r['strict']['F1']:.3f} | {r['strict']['random_flagged_35_49']:.2%} | {r['best_f1_random_flagged_35_49']:.2%} |"
          for n, r in results.items()],
        "",
        "### 5. Share of rows at each hop distance from the nearest known-illicit address (6 = none within 5)",
        "",
        "| Rows | 1 | 2 | 3 | 4 | 5 | none |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        *[f"| {g} | " + " | ".join(f"{v:.1%}" for v in s) + " |" for g, s in dist.items()],
        "",
        "Same for the nearest known-licit address:",
        "",
        "| Rows | 1 | 2 | 3 | 4 | 5 | none |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        *[f"| {g} | " + " | ".join(f"{v:.1%}" for v in s) + " |" for g, s in ldist.items()],
        "",
        "### Hop features by importance (share of total gain, + hops 1-5 model)",
        "",
        "| Feature | Gain share |",
        "| --- | --- |",
        *[f"| {f} | {v:.1%} |" for f, v in hop_gain.head(13).items()],
        "",
        f"All hop features together: {hop_gain.sum():.1%} of the model's gain.",
        "",
        "## What this means",
        "",
        CONCLUSIONS,
        "",
        *alert_section(),
        "## How the features are built",
        "",
        "- Transactions: for each training row (address, step), the 100 newest transactions touching the address in",
        "  the 4,032 blocks ending at the step's snapshot, the same transactions the wallet features use. Saved with",
        "  every input and output (address, value) under data/raw/bigquery/transactions/step=NNN/.",
        "- Graph per step: an address is linked to another when one paid the other in a saved transaction (each",
        "  input address to each output address; co-spending inputs are not linked). It contains nothing after the",
        "  step's snapshot. Addresses that are not training rows appear only through the transactions of rows, so",
        "  paths longer than one hop run through other addresses active in that step's snapshot.",
        "- Seeds: illicit = Elliptic++ class 1 from an earlier step + Ransomwhere addresses first seen before the",
        "  step; licit = Elliptic++ class 2 from an earlier step. Labels of the row's own step or later are never",
        "  used, and a row's own label never counts toward its features. OFAC is not used (no dates).",
        f"- Hubs: addresses with more than {HUB_COUNTERPARTIES:,} counterparties in the step's graph. A path can end at",
        "  a hub but never passes through one; seeds still reach their direct counterparties.",
        "- Multi-source BFS from the seeds, up to 5 hops. illicit_hop_dist: 1-5, 6 = none (hops 1-2 set: 1, 2,",
        "  3 = none within 2). illicit_within_1/_2: distinct seeds within 1 / 2 hops (exact).",
        "  illicit_btc_share_1hop: share of the row's BTC in its own transactions that came from or went to seeds",
        "  in the same transaction, split by value. illicit_decayed_2/_5: sum over the row's counterparties of",
        "  0.5^hops to the nearest seed through that counterparty. Same for licit_.",
        "- hop2_mean_log_*: mean of log1p(transactions / BTC received / counterparties) over 2-hop walks from the",
        "  row (not through hubs), using graph versions of those values computed from the saved transactions.",
        "- Approximation: for a row that is itself a seed, the nearest other seed is found through counterparties",
        "  whose nearest seed is a different address; a counterparty equally close to the row and to another seed",
        "  may be missed. Counts within 1-2 hops are exact.",
        "",
        "## Graph size",
        "",
        "| | Min | Median | Max |",
        "| --- | --- | --- | --- |",
        *[f"| {c.replace('_', ' ')} | {stats[c].min():,.0f} | {stats[c].median():,.0f} | {stats[c].max():,.0f} |"
          for c in ("transactions", "addresses", "links", "hubs", "rows", "rows_that_are_hubs", "illicit_seeds",
                    "illicit_seed_hubs", "licit_seeds", "seconds_total")],
        "",
        f"Hubs over all steps: {int(stats.hubs.sum()):,} (address, step) pairs, {int(stats.rows_that_are_hubs.sum()):,} of them training rows;"
        f" {int(stats.illicit_seed_hubs.sum()):,} illicit seeds were hubs.",
        "",
        "## Data and cost",
        "",
        f"- Transaction store: {store_rows:,} (step, transaction) rows, {store_bytes / 1e9:.2f} GB of Parquet.",
        f"- BigQuery billed for this work: {billed:.1f} GB (11 transaction queries + a 0.44 GB copy of the",
        "  active-address table). Each query was dry-run first and sent with a byte cap; see",
        "  data/processed/bigquery/cost_log.csv.",
        "",
        "## Rebuild",
        "",
        "```",
        "cd backend",
        "..\\.venv\\Scripts\\python.exe -m training.transaction_store plan",
        "..\\.venv\\Scripts\\python.exe -m training.transaction_store run",
        "..\\.venv\\Scripts\\python.exe -m training.hop_features",
        "..\\.venv\\Scripts\\python.exe -m training.hop_check",
        "..\\.venv\\Scripts\\python.exe -m training.alert_queue",
        "```",
        "",
    ]
    REPORT.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
