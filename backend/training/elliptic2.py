"""Elliptic2: subgraph classification with XGBoost and Random Forest.

Elliptic2 (Bellei et al., KDD MLF 2024; Kaggle ellipticco/elliptic2-data-set, CC BY-NC-ND 4.0) is a
graph of Bitcoin *clusters* (49.3M nodes) and transactions between them (196.2M edges), with 121,810
small labelled subgraphs (licit / suspicious money-flow paths). Features are binned and anonymised, so
this is a research model, separate from the live wallet model.

Run from backend/ (the zip stays zipped; the 83 GB edge file is streamed from it):
    python -m training.elliptic2 prepare --zip D:\\elliptic2\\elliptic2.zip --out D:\\elliptic2\\processed
    python -m training.elliptic2 train --out D:\\elliptic2\\processed

prepare: keeps node features of subgraph nodes, the features of subgraph edges, and for each subgraph
node its background in/out degree and the mean features of its background edges (1-hop context).
train:   one row per subgraph (size, shape, aggregated node/edge/context features), random 80/10/10 split
         as in the paper, XGBoost and Random Forest. Writes reports/elliptic2_models.md.
"""
import argparse
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.csv as pacsv

from training.build_training_dataset import BACKEND_DIR

REPORT = BACKEND_DIR / "reports" / "elliptic2_models.md"
SEED = 0


def stream_csv(z: zipfile.ZipFile, name: str, id_cols: list[str]):
    """Yield Arrow record batches; ids as int64, everything else float32.

    Reading through the zip is too slow for the big files, so each one is extracted next to the zip
    first (one at a time, to fit on the disk) and deleted afterwards.
    """
    target = Path(z.filename).parent / "csv" / name
    if not target.exists():
        print(f"extracting {name} ...", flush=True)
        target.parent.mkdir(exist_ok=True)
        z.extract(name, target.parent)
    with open(target, encoding="utf-8") as f:
        header = f.readline().strip().split(",")
    types = {c: (pa.int64() if c in id_cols else pa.float32()) for c in header}
    reader = pacsv.open_csv(target, read_options=pacsv.ReadOptions(block_size=64 << 20, use_threads=True),
                            convert_options=pacsv.ConvertOptions(column_types=types))
    yield from reader
    reader.close()
    target.unlink()  # free the space for the next file


def prepare(zip_path: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    z = zipfile.ZipFile(zip_path)
    nodes = pd.read_csv(z.open("nodes.csv"))
    edges = pd.read_csv(z.open("edges.csv"))
    components = pd.read_csv(z.open("connected_components.csv"))
    nodes.to_parquet(out / "nodes.parquet"); edges.to_parquet(out / "edges.parquet")
    components.to_parquet(out / "components.parquet")
    ids = np.unique(nodes.clId.to_numpy(np.int64))
    print(f"{len(components):,} subgraphs, {len(ids):,} subgraph nodes, {len(edges):,} subgraph edges", flush=True)

    # Node features of subgraph nodes (skipped if already done).
    kept = []
    if not (out / "node_features.parquet").exists():
        for batch in stream_csv(z, "background_nodes.csv", ["clId"]):
            cl = batch.column("clId").to_numpy()
            kept.append(batch.filter(pa.array(np.isin(cl, ids))))
    if not (out / "node_features.parquet").exists():
        pa.Table.from_batches(kept).to_pandas().to_parquet(out / "node_features.parquet")
    print("node features done", flush=True)

    # Background edges: 1-hop context per subgraph node, and the features of subgraph edges.
    sub_edge_keys = set(zip(edges.clId1, edges.clId2, edges.txId))
    n_feat, sums_out, sums_in = None, None, None
    deg_out = np.zeros(len(ids), np.int64); deg_in = np.zeros(len(ids), np.int64)
    sub_rows, rows_seen, started = [], 0, time.time()
    for batch in stream_csv(z, "background_edges.csv", ["clId1", "clId2", "txId"]):
        a = batch.column("clId1").to_numpy(); b = batch.column("clId2").to_numpy()
        feats = np.column_stack([batch.column(i).to_numpy(zero_copy_only=False) for i in range(3, batch.num_columns)])
        if n_feat is None:
            n_feat = feats.shape[1]
            sums_out = np.zeros((len(ids), n_feat), np.float64); sums_in = np.zeros((len(ids), n_feat), np.float64)
            feat_names = batch.schema.names[3:]
        for col, deg, sums in ((a, deg_out, sums_out), (b, deg_in, sums_in)):
            pos = np.clip(np.searchsorted(ids, col), 0, len(ids) - 1)
            hit = ids[pos] == col
            if hit.any():
                np.add.at(deg, pos[hit], 1)
                np.add.at(sums, pos[hit], np.nan_to_num(feats[hit]))
        both = np.isin(a, ids) & np.isin(b, ids)
        if both.any():
            part = batch.filter(pa.array(both)).to_pandas()
            sub_rows.append(part[[k in sub_edge_keys for k in zip(part.clId1, part.clId2, part.txId)]])
        rows_seen += batch.num_rows
        if rows_seen // 10_000_000 != (rows_seen - batch.num_rows) // 10_000_000:
            print(f"  edges read: {rows_seen/1e6:.0f}M of ~196M ({(time.time()-started)/60:.0f} min)", flush=True)
    pd.concat(sub_rows).to_parquet(out / "subgraph_edge_features.parquet")
    context = pd.DataFrame({"clId": ids, "bg_out_degree": deg_out, "bg_in_degree": deg_in})
    with np.errstate(invalid="ignore", divide="ignore"):
        mean_out = sums_out / np.maximum(deg_out, 1)[:, None]; mean_in = sums_in / np.maximum(deg_in, 1)[:, None]
    for i, name in enumerate(feat_names):
        context[f"out_mean_{name}"] = mean_out[:, i].astype("float32")
        context[f"in_mean_{name}"] = mean_in[:, i].astype("float32")
    context.to_parquet(out / "node_context.parquet")
    print(f"prepare done: {rows_seen:,} background edges read", flush=True)


def subgraph_table(out: Path) -> pd.DataFrame:
    nodes = pd.read_parquet(out / "nodes.parquet")
    comps = pd.read_parquet(out / "components.parquet")
    nf = nodes.merge(pd.read_parquet(out / "node_features.parquet"), on="clId", how="left")
    nf = nf.merge(pd.read_parquet(out / "node_context.parquet"), on="clId", how="left")
    edges = pd.read_parquet(out / "subgraph_edge_features.parquet")
    edges = edges.merge(nodes.rename(columns={"clId": "clId1"}), on="clId1")
    value_cols = [c for c in nf.columns if c not in ("clId", "ccId")]
    agg_nodes = nf.groupby("ccId")[value_cols].agg(["mean", "max", "min"])
    agg_nodes.columns = [f"node_{c}_{s}" for c, s in agg_nodes.columns]
    edge_cols = [c for c in edges.columns if c.startswith("feat")]
    agg_edges = edges.groupby("ccId")[edge_cols].agg(["mean", "max"])
    agg_edges.columns = [f"edge_{c}_{s}" for c, s in agg_edges.columns]
    shape = pd.DataFrame({
        "n_nodes": nodes.groupby("ccId").size(),
        "n_edges": edges.groupby("ccId").size(),
        "n_txs": edges.groupby("ccId").txId.nunique(),
    })
    out_deg = edges.groupby(["ccId", "clId1"]).size().groupby("ccId").agg(["max", "mean"])
    in_deg = edges.groupby(["ccId", "clId2"]).size().groupby("ccId").agg(["max", "mean"])
    shape["max_out_deg"], shape["mean_out_deg"] = out_deg["max"], out_deg["mean"]
    shape["max_in_deg"], shape["mean_in_deg"] = in_deg["max"], in_deg["mean"]
    table = comps.set_index("ccId").join([shape, agg_nodes, agg_edges]).fillna(0)
    table["y"] = (table.ccLabel == "suspicious").astype(int)
    return table.drop(columns="ccLabel")


def train(out: Path) -> None:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import average_precision_score, f1_score, roc_auc_score
    from xgboost import XGBClassifier

    from training.compare_models import best_threshold

    table = subgraph_table(out)
    cols = [c for c in table.columns if c != "y"]
    rng = np.random.default_rng(SEED)
    split = rng.choice(["train", "val", "test"], size=len(table), p=[0.8, 0.1, 0.1])
    tr, va, te = table[split == "train"], table[split == "val"], table[split == "test"]
    ratio = (tr.y == 0).sum() / max(tr.y.sum(), 1)
    models = {
        "XGBoost": XGBClassifier(n_estimators=800, learning_rate=0.05, max_depth=6, min_child_weight=5,
                                 subsample=0.85, colsample_bytree=0.7, reg_lambda=5, scale_pos_weight=ratio * 0.25,
                                 tree_method="hist", n_jobs=-1, random_state=SEED),
        "Random Forest": RandomForestClassifier(n_estimators=400, min_samples_leaf=2, max_features="sqrt",
                                                class_weight="balanced_subsample", n_jobs=-1, random_state=SEED),
    }
    rows = []
    for name, model in models.items():
        print(f"training {name} on {len(tr):,} subgraphs, {len(cols)} features ...", flush=True)
        model.fit(tr[cols], tr.y)
        t = best_threshold(va.y.to_numpy(), model.predict_proba(va[cols])[:, 1])
        p = model.predict_proba(te[cols])[:, 1]
        rows.append((name, average_precision_score(te.y, p), roc_auc_score(te.y, p),
                     f1_score(te.y, p >= t), f1_score(te.y, p >= t, average="micro"), t))
        if name == "XGBoost":
            top = pd.Series(model.feature_importances_, index=cols).nlargest(10)
    lines = [
        "# Elliptic2 subgraph models",
        "",
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by training/elliptic2.py.",
        f"{len(table):,} labelled subgraphs ({int(table.y.sum()):,} suspicious), random 80/10/10 split as in the paper",
        f"(test: {len(te):,} subgraphs, {int(te.y.sum())} suspicious). One row per subgraph: size and shape, node",
        "features, subgraph-edge features, and each node's background context (degree and mean edge features).",
        "Threshold picked on the validation split. Paper baselines did not use node/edge features.",
        "",
        "| Model | PR-AUC | ROC-AUC | F1 (suspicious) | F1 (micro, as in paper) |",
        "| --- | --- | --- | --- | --- |",
        *[f"| {n} | {pr:.3f} | {roc:.3f} | {f1:.3f} | {mf1:.3f} |" for n, pr, roc, f1, mf1, _ in rows],
        "| GLASS (paper, test) | 0.208 | 0.889 | n/a | 0.933 |",
        "| GNN-Seg (paper, test) | 0.026 | 0.537 | n/a | 0.398 |",
        "",
        "Top XGBoost features: " + ", ".join(f"{c} ({v:.3f})" for c, v in top.items()),
        "",
        "Elliptic2 nodes are Elliptic's private address clusters with binned, anonymised features, so this model",
        "cannot score live mempool.space lookups. It answers a different question: is this money-flow path suspicious?",
        "",
    ]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=["prepare", "train"])
    parser.add_argument("--zip", type=Path, default=Path(r"D:\elliptic2\elliptic2.zip"))
    parser.add_argument("--out", type=Path, default=Path(r"D:\elliptic2\processed"))
    args = parser.parse_args()
    prepare(args.zip, args.out) if args.mode == "prepare" else train(args.out)


if __name__ == "__main__":
    main()
