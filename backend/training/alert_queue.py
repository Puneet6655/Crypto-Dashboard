"""Alert-queue data: known-illicit addresses and high-scoring addresses near them, with evidence.

Run from backend/ after training.hop_check (uses the feature set it found best):
    python -m training.alert_queue [--features "+ hops 1-2"]

Model: tuned XGBoost trained on every Elliptic++ label (steps 1-49) with v2+exp + hop features.
Strict threshold: the score that flags 1% of random active addresses in steps 1-49.
Rows of steps 1-49 were scored by a model that saw their labels (score_in_training = true).

Writes (gitignored) data/processed/alert_queue/:
  addresses.parquet: one row per (address, step) that is
      known_illicit: the address was a known-illicit label before the step (Elliptic++ class 1 from an
                     earlier step, or Ransomwhere first seen earlier) and was active in the step, or
      near_illicit:  within 2 hops of such an address and scored at or above the strict threshold.
  transactions.parquet: every saved transaction of that step touching an alert address, plus every
      evidence transaction (alert_addresses is empty for evidence that does not touch one).
"""
import argparse
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from training import hop_features
from training.build_training_dataset import BACKEND_DIR, DATASET
from training.hop_check import SETS, load
from training.improvement_check import MODELS, train
from training.transaction_store import STORE

OUT = BACKEND_DIR / "data" / "processed" / "alert_queue"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--features", default=None, help="feature set name from training/hop_check.py")
    args = parser.parse_args()
    summary_path = hop_features.OUT / "eval_summary.json"
    name = args.features
    if name is None:
        results = json.loads(summary_path.read_text())["results"]
        hop_sets = {k: v for k, v in results.items() if k != "v2+exp"}
        # Prefer the smaller set unless the 1-5 set is clearly better (> 0.005 PR-AUC trained once).
        name = "+ hops 1-2"
        if hop_sets["+ hops 1-5"]["static"]["PR-AUC"] > hop_sets["+ hops 1-2"]["static"]["PR-AUC"] + 0.005:
            name = "+ hops 1-5"
    cols = SETS[name]
    builder, params = MODELS["XGBoost (tuned)"]
    print(f"feature set {name}", flush=True)

    df = load()
    extra = pd.read_parquet(DATASET, columns=["address", "step", "snap_end_time", "anchor_block", "external_family"])
    extra["address"] = extra["address"].astype(str)
    df = df.merge(extra, on=["address", "step"], how="left")
    labelled = df[df.label.notna()].copy()
    labelled["label"] = labelled.label.astype(int)
    model = train(builder, params, labelled, cols)
    df["score"] = model.predict_proba(df[cols])[:, 1].astype("float32")
    random_rows = df[(df.source == "bq_same_era") & df.known_elliptic_class.isna() & df.external_label.isna()]
    strict = float(random_rows.score.quantile(0.99))
    print(f"strict threshold {strict:.4f}", flush=True)

    table = hop_features.labels()
    seed_rows = df[["address", "step"]].merge(table, on="address")
    seed_rows = seed_rows[seed_rows.known_after_step < seed_rows.step]
    illicit_seed = seed_rows[seed_rows.cls == 1].drop_duplicates(["address", "step"])
    is_seed = df.set_index(["address", "step"]).index.isin(illicit_seed.set_index(["address", "step"]).index)

    paths = pd.concat([pd.read_parquet(p, columns=["address", "step", "illicit_nearest_seed", "illicit_path_txs"])
                       for p in sorted(hop_features.OUT.glob("step=*.parquet"))], ignore_index=True)
    df = df.merge(paths, on=["address", "step"], how="left")
    hop = df.illicit_hop_dist.to_numpy()
    near = (~is_seed) & (hop <= 2) & (df.score.to_numpy() >= strict)
    alerts = df[is_seed | near].copy()
    alerts["reason"] = np.where(is_seed[is_seed | near], "known_illicit", "near_illicit")

    # Where each label came from: Elliptic++ (class 1) or Ransomwhere (with family).
    source_of = {a: "elliptic_pp" for a in table.loc[table.cls == 1, "address"]}
    ext = df.loc[df.external_label.fillna("").str.contains("ransomware"), "address"]
    source_of.update({a: "ransomwhere" for a in ext})
    known = alerts.reason == "known_illicit"
    alerts["hop_distance"] = np.where(known, 0, alerts.illicit_hop_dist).astype("int8")
    alerts["nearest_seed"] = np.where(known, alerts.address, alerts.illicit_nearest_seed)
    alerts["nearest_seed_source"] = alerts.nearest_seed.map(source_of)
    alerts["evidence_tx_hashes"] = [list(x) if isinstance(x, (list, np.ndarray)) else [] for x in alerts.illicit_path_txs]
    alerts["above_threshold"] = alerts.score >= strict
    alerts["threshold"] = np.float32(strict)
    alerts["score_in_training"] = alerts.label.notna() & (alerts.step <= 49)
    alerts["elliptic_label"] = alerts.label.astype("Int8")
    alerts["alert_id"] = alerts.step.astype(str).str.zfill(3) + ":" + alerts.address
    alerts["model_features"] = name
    alerts["created_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out_cols = ["alert_id", "address", "step", "snap_end_time", "anchor_time", "anchor_block", "reason", "score",
                "threshold", "above_threshold", "hop_distance", "nearest_seed", "nearest_seed_source",
                "evidence_tx_hashes", "illicit_within_1", "illicit_within_2", "illicit_btc_share_1hop",
                "licit_hop_dist", "elliptic_label", "external_family", "score_in_training", "model_features",
                "created_utc"]
    alerts = alerts[out_cols].sort_values(["step", "reason", "score"], ascending=[True, True, False])
    alerts["illicit_within_1"] = alerts.illicit_within_1.astype("int32")
    alerts["illicit_within_2"] = alerts.illicit_within_2.astype("int32")
    alerts["licit_hop_dist"] = alerts.licit_hop_dist.astype("int8")
    OUT.mkdir(parents=True, exist_ok=True)
    alerts.to_parquet(OUT / "addresses.parquet", index=False)
    print(alerts.groupby("reason").size().to_string(), flush=True)

    # Transactions touching alert addresses, per step.
    writer = None
    evidence = set(h for hs in alerts.evidence_tx_hashes for h in hs)
    n_tx = 0
    for step, group in alerts.groupby("step"):
        wanted = pa.array(sorted(group.address.unique()))
        t = pq.read_table(STORE / f"step={step:03d}" / "part-0.parquet")
        hits = []
        for side in ("inputs", "outputs"):
            col = t.column(side).combine_chunks()
            parent = pc.list_parent_indices(col)
            addr = pc.list_flatten(col).field("address")
            mask = pc.is_in(addr, value_set=wanted)
            hits.append(pd.DataFrame({"row": pc.filter(parent, mask).to_numpy(),
                                      "address": pc.filter(addr, mask).to_numpy(zero_copy_only=False)}))
        hits = pd.concat(hits).drop_duplicates()
        by_row = hits.groupby("row")["address"].apply(lambda s: sorted(s))
        # Evidence transactions further along the path (intermediate address -> seed) are kept too.
        step_evidence = pa.array(sorted({h for hs in group.evidence_tx_hashes for h in hs}), type=pa.string())
        ev_rows = np.flatnonzero(pc.is_in(t.column("tx_hash"), value_set=step_evidence).to_numpy(zero_copy_only=False))
        by_row = by_row.reindex(np.union1d(by_row.index.to_numpy(), ev_rows))
        by_row = by_row.apply(lambda v: v if isinstance(v, list) else [])
        sub = t.take(pa.array(by_row.index.to_numpy())).to_pandas()
        sub["alert_addresses"] = by_row.to_numpy()
        sub["is_evidence"] = sub.tx_hash.isin(evidence)
        sub["total_input_btc"] = [sum(x["value"] or 0 for x in v) / 1e8 for v in sub.inputs]
        sub["total_output_btc"] = [sum(x["value"] or 0 for x in v) / 1e8 for v in sub.outputs]
        sub = sub[["step", "tx_hash", "block_height", "block_time", "is_coinbase", "fee_sats", "total_input_btc",
                   "total_output_btc", "inputs", "outputs", "alert_addresses", "is_evidence"]]
        part = pa.Table.from_pandas(sub, preserve_index=False)
        if writer is None:
            writer = pq.ParquetWriter(OUT / "transactions.parquet", part.schema, compression="zstd")
        writer.write_table(part.cast(writer.schema))
        n_tx += len(sub)
    writer.close()
    print(f"saved {len(alerts):,} alert rows and {n_tx:,} transactions to {OUT}", flush=True)


if __name__ == "__main__":
    main()
