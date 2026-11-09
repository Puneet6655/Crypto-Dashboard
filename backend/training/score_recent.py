"""Batch-score recent Bitcoin addresses from the BigQuery recent store, and re-pick the alert threshold.

Run from backend/ after training.recent_store pull (about 30-60 minutes for the default sample):
    ..\\.venv\\Scripts\\python.exe -m training.score_recent                  # 100,000 random addresses
    ..\\.venv\\Scripts\\python.exe -m training.score_recent --sample 5000    # quick check

Who is scored: addresses active in the newest week of the store (1,008 blocks ending at its tip).
That is about 3 million addresses, so a random sample (seed 0, default 100,000; the hop graph of more does not
fit in 16 GB) is scored, plus every
active address on a known list (OFAC, Ransomwhere, CryptoScamDB BTC payment addresses, Elliptic++
labels), which are kept apart from the random sample.

Features, with the training rules (app/features/wallet_window.py, same functions):
  * wallet window: the newest 100 transactions in the 4,032 blocks ending at the address's newest
    transaction (always inside the newest week), observed at the store's tip;
  * address type, exposure to known-label counterparties (every label: scoring is after all of them),
    network context (144 blocks before the anchor, counted from the store, which holds every transaction);
  * hop features for the alert model (training.hop_features.StepGraph), on the graph of every kept
    transaction of every scored address, as in training (there: every address active in a snapshot).
    Seeds: known illicit (Elliptic++ class 1, Ransomwhere, OFAC) and licit (Elliptic++ class 2) addresses.
    Most 2016-2017 seeds are no longer active, so few recent rows are near one.
Models: xgboost__v2_exp, random_forest__v2_exp and the alert model xgboost__alert_hops.
Threshold: the random sample is split in two halves by address hash; the new threshold is the score that
flags 1% of half A (the rule of alert_threshold.recent_threshold), checked on half B.

Writes (gitignored) data/processed/recent_scores/: features.parquet, scores.parquet, summary.json, and
graph_txs.parquet (the graph's transactions). Adds recent_2026_threshold to models/registry.json
(alert_threshold and the two v2+exp entries) without changing existing keys; --no-registry skips that.
"""
import argparse
import hashlib
import json
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from app.features.wallet_window import (
    MAX_TXS, NETWORK_BLOCKS, SATS_PER_BTC, WEEK_BLOCKS, WINDOW_BLOCKS, TxRecord, address_type_features,
    compute_wallet_features, exposure_features, window_counterparties,
)
from app.ml.known_labels import FILE_NAME as KNOWN_FILE
from app.ml.known_labels import load as load_known
from app.ml.registry import ModelRegistry
from training.bq_common import BACKEND_DIR
from training.hop_features import HOP_COLS_5, StepGraph
from training.recent_store import SPAN_BLOCKS, covered_ranges, load_manifest, read_blocks

OUT = BACKEND_DIR / "data" / "processed" / "recent_scores"
MODELS_DIR = BACKEND_DIR / "models"
MODEL_IDS = {"v2_exp": "xgboost__v2_exp", "rf_v2_exp": "random_forest__v2_exp", "alert": "xgboost__alert_hops"}
RATE = 0.01  # the threshold flags 1% of random recent addresses


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def scam_btc_addresses() -> set[str]:
    from app.config import get_settings
    from app.intelligence import snapshots
    from app.intelligence.sources import SOURCES

    records = snapshots.load_records(get_settings().indicators_dir, SOURCES["cryptoscamdb_blacklist"])
    return {a for r in records for c, a in (getattr(r, "addresses", None) or ()) if c.upper() == "BTC"}


def addresses_of(table: pa.Table) -> pa.Array:
    parts = []
    for side in ("inputs", "outputs"):
        flat = pc.list_flatten(table.column(side))
        parts.append(pc.struct_field(flat, "address"))
    return pc.unique(pa.chunked_array([c for p in parts for c in p.chunks]).drop_null())


def active_addresses(tip: int) -> pa.Array:
    uniques = [addresses_of(t) for _, t in read_blocks(tip - WEEK_BLOCKS + 1, tip, ["inputs", "outputs"])]
    return pc.unique(pa.chunked_array(uniques))


def in_half_a(addresses: pd.Series) -> np.ndarray:
    """Stable 50/50 split by address hash (for picking vs checking the threshold)."""
    return np.array([hashlib.blake2b(a.encode(), digest_size=2).digest()[0] < 128 for a in addresses])


def pick_targets(active: pa.Array, sample: int, listed: dict[str, set[str]], elliptic: pd.Series) -> pd.DataFrame:
    n = len(active)
    ordered = pc.take(active, pc.sort_indices(active)).to_numpy(zero_copy_only=False)
    rng = np.random.default_rng(0)
    chosen = ordered[np.sort(rng.choice(n, size=min(sample, n), replace=False))]
    active_set = pa.array(ordered)
    extra = set()
    for addrs in listed.values():
        extra |= addrs
    extra |= set(elliptic.index)
    extra_arr = pa.array(sorted(extra), pa.string())
    extra_active = extra_arr.filter(pc.is_in(extra_arr, value_set=active_set)).to_numpy(zero_copy_only=False)
    targets = pd.DataFrame({"address": pd.unique(np.concatenate([chosen, extra_active]))})
    targets["in_random"] = targets.address.isin(set(chosen))
    src = []
    for a in targets.address:
        names = [name for name, addrs in listed.items() if a in addrs]
        src.append("|".join(names) if names else None)
    targets["listed_sources"] = src
    targets["elliptic_class"] = targets.address.map(elliptic).astype("Int8")
    targets["half_a"] = in_half_a(targets.address)
    return targets


def collect_pairs(need_lo: int, tip: int, target_arr: pa.Array) -> tuple[pd.DataFrame, list[str]]:
    """Pass 1: (target, file, row, height, tx_hash) for every stored transaction touching a target."""
    pair_frames, hash_frames, files = [], [], []
    for fi, (name, t) in enumerate(read_blocks(need_lo, tip, ["tx_hash", "block_height", "inputs", "outputs"])):
        files.append(name)
        heights = t.column("block_height").to_numpy()
        sides = []
        for side in ("inputs", "outputs"):
            col = t.column(side).combine_chunks()
            parent = pc.list_parent_indices(col).to_numpy()
            code = pc.index_in(pc.struct_field(pc.list_flatten(col), "address"), value_set=target_arr)
            hit = code.is_valid().to_numpy(zero_copy_only=False)
            rows = parent[hit]
            sides.append(pd.DataFrame({"target": code.filter(code.is_valid()).to_numpy().astype(np.int32),
                                       "file": np.int16(fi), "row": rows.astype(np.int32),
                                       "height": heights[rows].astype(np.int32)}))
        frame = pd.concat(sides, ignore_index=True).drop_duplicates(["target", "row"])
        pair_frames.append(frame)
        hit_rows = np.unique(frame.row.to_numpy())  # their hashes are needed for the tie-break
        hash_frames.append(pd.DataFrame({"file": np.int16(fi), "row": hit_rows.astype(np.int32), "tx_hash": pc.take(
            t.column("tx_hash"), pa.array(hit_rows)).to_numpy(zero_copy_only=False)}))
        log(f"  pass 1: {name} ({t.num_rows:,} txs, {len(frame):,} pairs)")
    pairs = pd.concat(pair_frames, ignore_index=True)
    pairs = pairs.merge(pd.concat(hash_frames, ignore_index=True), on=["file", "row"], how="left",
                        validate="many_to_one")
    # The first-pull files split blocks at their edges, so a tx is in one file only: no cross-file duplicates.
    return pairs, files


def keep_window(pairs: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The wallet-window rule: newest 100 txs in the 4,032 blocks ending at each target's newest tx."""
    pairs = pairs.sort_values(["target", "height", "tx_hash"], ascending=[True, False, False], kind="mergesort")
    anchor = pairs.groupby("target", sort=False)["height"].transform("first")
    pairs = pairs[pairs.height >= anchor - WINDOW_BLOCKS + 1].copy()
    pairs["rank"] = pairs.groupby("target", sort=False).cumcount()
    per_target = pairs.groupby("target").agg(anchor_block=("height", "first"), in_window=("height", "size"))
    per_target["window_truncated"] = per_target.in_window > MAX_TXS
    return pairs[pairs["rank"] < MAX_TXS].reset_index(drop=True), per_target


def load_kept(kept: pd.DataFrame, need_lo: int, tip: int) -> pa.Table:
    """Pass 2: full rows of every kept transaction (one copy each), with a 'kid' index column."""
    wanted = kept[["file", "row"]].drop_duplicates().sort_values(["file", "row"])
    parts = []
    for fi, (name, t) in enumerate(read_blocks(need_lo, tip, ["tx_hash", "block_height", "block_time", "is_coinbase",
                                                               "fee_sats", "vsize", "inputs", "outputs"])):
        rows = wanted.loc[wanted.file == fi, "row"].to_numpy()
        if len(rows):
            part = t.take(pa.array(rows))
            parts.append(part.append_column("file", pa.array(np.full(len(rows), fi, np.int16)))
                         .append_column("row", pa.array(rows.astype(np.int32))))
    table = pa.concat_tables(parts)
    return table.append_column("kid", pa.array(np.arange(table.num_rows, dtype=np.int64)))


def tx_records(table: pa.Table) -> list[TxRecord]:
    """Arrow rows -> TxRecord, with the same rules as the BigQuery SQL (all inputs count for value and count)."""
    out = []
    cols = {c: table.column(c).to_pylist() for c in ("tx_hash", "block_height", "block_time", "is_coinbase",
                                                       "fee_sats", "vsize", "inputs", "outputs")}
    for i in range(table.num_rows):
        ins, outs = cols["inputs"][i], cols["outputs"][i]
        coinbase = bool(cols["is_coinbase"][i])
        out.append(TxRecord(
            txid=cols["tx_hash"][i], block_height=int(cols["block_height"][i]), block_time=cols["block_time"][i],
            fee_sats=0 if coinbase else int(cols["fee_sats"][i] or 0),
            input_value_sats=0 if coinbase else sum(int(x["value"] or 0) for x in ins),
            inputs=tuple((x["address"], int(x["value"] or 0)) for x in ins if x["address"] is not None),
            outputs=tuple((x["address"], int(x["value"] or 0)) for x in outs if x["address"] is not None),
            vsize=int(cols["vsize"][i] or 0), input_count=len(ins), output_count=len(outs)))
    return out


def block_stats(need_lo: int, tip: int) -> tuple[int, np.ndarray, np.ndarray]:
    """Per-block transaction count (with the coinbase) and total fees, for blocks need_lo..tip."""
    n = tip - need_lo + 1
    tx_count, fees = np.zeros(n, np.int64), np.zeros(n, np.int64)
    for _, t in read_blocks(need_lo, tip, ["block_height", "fee_sats"]):
        h = t.column("block_height").to_numpy() - need_lo
        tx_count += np.bincount(h, minlength=n)
        fees += np.bincount(h, weights=t.column("fee_sats").to_numpy(zero_copy_only=False), minlength=n).astype(np.int64)
    return need_lo, tx_count, fees


def network_features(anchors: np.ndarray, fees_mean: np.ndarray, stats, strict: bool = True) -> dict:
    """net_tx_count_144, net_avg_fee_btc_144, fee_vs_network_mean (same formula as training)."""
    lo, tx_count, fees = stats
    cum_tx = np.concatenate([[0], np.cumsum(tx_count)])
    cum_fee = np.concatenate([[0], np.cumsum(fees)])
    end = anchors - lo + 1
    start = end - NETWORK_BLOCKS
    if start.min() < 0:
        if strict:
            raise ValueError("store does not reach 144 blocks before the earliest anchor")
        start = np.maximum(start, 0)  # test runs on a short span only
    n_tx = cum_tx[end] - cum_tx[start]
    avg_fee = (cum_fee[end] - cum_fee[start]) / np.maximum(n_tx - NETWORK_BLOCKS, 1) / SATS_PER_BTC
    return {"net_tx_count_144": n_tx.astype(float), "net_avg_fee_btc_144": avg_fee,
            "fee_vs_network_mean": np.where(avg_fee > 0, fees_mean / np.where(avg_fee > 0, avg_fee, 1), 0.0)}


def wallet_features(targets: pd.DataFrame, kept: pd.DataFrame, records: list[TxRecord], known) -> pd.DataFrame:
    by_target = kept.sort_values(["target", "rank"]).groupby("target")["kid"].apply(list)
    rows = []
    started = time.time()
    for i, (target, kids) in enumerate(by_target.items()):
        address = targets.address.iat[target]
        txs = [records[k] for k in kids]
        f = compute_wallet_features(address, txs)
        f.update(address_type_features(address))
        payers, payees = window_counterparties(address, txs)
        f.update(exposure_features(payers, payees, known.illicit, known.licit))
        f["target"] = target
        f["anchor_time"] = txs[0].block_time  # kept newest first
        rows.append(f)
        if (i + 1) % 25_000 == 0:
            log(f"  features: {i + 1:,}/{len(by_target):,} ({time.time() - started:.0f} s)")
    return pd.DataFrame(rows).set_index("target")


def graph_file(targets: pd.DataFrame, kept: pd.DataFrame, txs: pa.Table) -> tuple[str, dict]:
    """Write the kept transactions with row_addresses (the scored addresses each one was kept for)."""
    order = kept.sort_values("kid")
    counts = np.bincount(order.kid.to_numpy(), minlength=txs.num_rows)
    offsets = pa.array(np.concatenate([[0], np.cumsum(counts)]).astype(np.int32))
    values = pa.array(targets.address.to_numpy()[order.target.to_numpy()], pa.string())
    table = pa.table({"tx_hash": txs.column("tx_hash"), "inputs": txs.column("inputs"), "outputs": txs.column("outputs"),
                      "row_addresses": pa.ListArray.from_arrays(offsets, values)})
    path = OUT / "graph_txs.parquet"
    pq.write_table(table, path, compression="zstd")
    return str(path), {"transactions": table.num_rows}


def hop_features(path: str, targets: pd.DataFrame, known) -> tuple[pd.DataFrame, dict]:
    t0 = time.time()
    g = StepGraph(path=path)
    rows = g.nodes_of(targets.address)
    if (rows < 0).any():
        raise RuntimeError(f"{(rows < 0).sum()} scored addresses are missing from the graph")
    illicit = g.nodes_of(sorted(known.illicit))
    licit = g.nodes_of(sorted(known.licit))
    illicit, licit = np.unique(illicit[illicit >= 0]), np.unique(licit[licit >= 0])
    data = {"address": targets.address.to_numpy()}
    data.update(g.seed_features(illicit, rows, "illicit", want_paths=True))
    data.update(g.seed_features(licit, rows, "licit"))
    data.update(g.neighbour_means(rows))
    stats = {"transactions": len(g.tx_hash), "addresses": g.n, "links": len(g.src) // 2, "hubs": int(g.hub.sum()),
             "illicit_seeds_in_graph": len(illicit), "licit_seeds_in_graph": len(licit),
             "seconds": round(time.time() - t0, 1)}
    return pd.DataFrame(data), stats


def top_shap(model, X: pd.DataFrame, n: int = 5) -> list[str]:
    import shap

    if X.empty:
        return []
    values = np.asarray(shap.TreeExplainer(model).shap_values(X))
    if values.ndim == 3:
        values = values[..., 1]
    out = []
    for row in values:
        idx = np.argsort(-np.abs(row))[:n]
        out.append(json.dumps([{"feature": X.columns[i], "contribution": round(float(row[i]), 4)} for i in idx]))
    return out


def thresholds(scores: pd.DataFrame, entries: dict) -> dict:
    rand = scores[scores.in_random]
    half_a, half_b = rand[rand.half_a], rand[~rand.half_a]
    listed = scores[scores.listed_sources.notna()]
    out = {}
    for key, entry in entries.items():
        col = f"score_{key}"
        new = float(np.quantile(half_a[col], 1 - RATE))
        olds = ({"recent_threshold_steps_50_75": entry["recent_threshold"], "old_threshold_steps_1_49": entry["old_threshold"]}
                if key == "alert" else {"best_f1_threshold": entry["threshold"]})
        table = {}
        for name, t in {**olds, "new": new}.items():
            table[name] = {
                "threshold": round(float(t), 4),
                "random_flagged": int((rand[col] >= t).sum()), "random_flagged_share": round(float((rand[col] >= t).mean()), 4),
                "half_b_flagged_share": round(float((half_b[col] >= t).mean()), 4),
                "listed_caught": int((listed[col] >= t).sum()),
                "listed_caught_by_source": {s: int(((listed[col] >= t) & listed.listed_sources.str.contains(s)).sum())
                                            for s in ("ofac", "ransomwhere", "scam")},
            }
        out[entry["id"]] = {"new": round(new, 4), "rule": f"flags {RATE:.0%} of random recent active addresses "
                            "(half A of the sample, by address hash)", "at": table}
    return out


def update_registry(summary: dict) -> None:
    path = MODELS_DIR / "registry.json"
    reg = json.loads(path.read_text())
    meta = {"recent_2026_rule": "flags 1% of random addresses active in the newest week of the BigQuery recent store",
            "recent_2026_tip_block": summary["store"]["tip"], "recent_2026_picked_utc": summary["generated_utc"],
            "recent_2026_random_rows": summary["counts"]["random_scored"]}
    for model_id, t in summary["thresholds"].items():
        if model_id == reg["alert_threshold"]["model_id"]:
            reg["alert_threshold"]["recent_2026_threshold"] = t["new"]
            reg["alert_threshold"].update(meta)
        for e in reg["models"]:
            if e["id"] == model_id:
                e["recent_2026_threshold"] = t["new"]
                e.update(meta)
    path.write_text(json.dumps(reg, indent=2))
    log(f"registry updated: {path}")


def main() -> None:
    global OUT
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sample", type=int, default=100_000)
    parser.add_argument("--no-registry", action="store_true")
    parser.add_argument("--span", type=int, default=SPAN_BLOCKS, help="blocks to use (testing only; default the full span)")
    parser.add_argument("--tip", type=int, default=None, help="newest block to use (testing only)")
    args = parser.parse_args()
    started = time.time()
    timings = {}
    OUT.mkdir(parents=True, exist_ok=True)

    manifest = load_manifest()
    covered = covered_ranges(manifest)
    tip = args.tip or covered[-1][1]
    need_lo = tip - args.span + 1
    if not any(lo <= need_lo and hi >= tip for lo, hi in covered):
        raise SystemExit(f"store {covered} does not cover blocks {need_lo}-{tip}; run training.recent_store pull")
    log(f"store tip {tip:,}; window blocks {need_lo:,}-{tip:,}; active week from {tip - WEEK_BLOCKS + 1:,}")

    known = load_known(MODELS_DIR)
    table = pd.read_parquet(MODELS_DIR / KNOWN_FILE)
    listed = {"ofac": set(table.loc[table.source == "ofac_xbt", "address"]),
              "ransomwhere": set(table.loc[table.source == "ransomwhere", "address"]),
              "scam": scam_btc_addresses()}
    ell_path = BACKEND_DIR / "data" / "raw" / "elliptic_pp" / "wallets_classes.csv"
    elliptic = pd.read_csv(ell_path, dtype={"address": str}).drop_duplicates("address").set_index("address")["class"]
    elliptic = elliptic[elliptic.isin([1, 2])]

    if args.span < SPAN_BLOCKS or args.tip:
        OUT = BACKEND_DIR / "data" / "processed" / "recent_scores_test"  # never overwrite a real run
        OUT.mkdir(parents=True, exist_ok=True)
    t = time.time()
    active = active_addresses(tip)
    targets = pick_targets(active, args.sample, listed, elliptic)
    timings["active_and_sample"] = round(time.time() - t)
    log(f"{len(active):,} active addresses in the newest week; scoring {len(targets):,} "
        f"({int(targets.in_random.sum()):,} random, {int(targets.listed_sources.notna().sum()):,} listed, "
        f"{int(targets.elliptic_class.notna().sum()):,} Elliptic++-labelled)")

    t = time.time()
    pairs, files = collect_pairs(need_lo, tip, pa.array(targets.address, pa.string()))
    kept, per_target = keep_window(pairs)
    del pairs
    txs = load_kept(kept, need_lo, tip)
    kid = pd.DataFrame({"file": txs.column("file").to_numpy(), "row": txs.column("row").to_numpy(),
                        "kid": txs.column("kid").to_numpy()})
    kept = kept.merge(kid, on=["file", "row"], how="left", validate="many_to_one")
    timings["select_transactions"] = round(time.time() - t)
    log(f"{len(kept):,} (address, tx) pairs kept, {txs.num_rows:,} distinct transactions")

    t = time.time()
    records = tx_records(txs)
    feats = wallet_features(targets, kept, records, known)
    del records
    stats = block_stats(need_lo, tip)
    net = network_features(feats.anchor_block.to_numpy(np.int64), feats.fees_mean.to_numpy(), stats,
                           strict=args.span == SPAN_BLOCKS)
    for k, v in net.items():
        feats[k] = v
    # The feature code only sees the kept 100 transactions, so it cannot tell the window held more.
    feats["window_truncated"] = per_target.loc[feats.index, "window_truncated"].astype(float).to_numpy()
    timings["wallet_features"] = round(time.time() - t)
    if not (feats.anchor_block.to_numpy() == per_target.loc[feats.index, "anchor_block"].to_numpy()).all():
        raise RuntimeError("anchor mismatch between the selection and the feature code")
    log(f"wallet features for {len(feats):,} addresses")

    t = time.time()
    path, _ = graph_file(targets, kept, txs)
    del txs
    hops, graph_stats = hop_features(path, targets, known)
    timings["hop_features"] = round(time.time() - t)
    log(f"hop features: {graph_stats}")

    df = targets.join(feats, how="inner")
    df = df.merge(hops.drop(columns=["illicit_nearest_seed", "illicit_path_txs"]), on="address", validate="one_to_one")
    df = df.merge(hops[["address", "illicit_nearest_seed"]], on="address")
    df.to_parquet(OUT / "features.parquet", index=False)

    t = time.time()
    registry = ModelRegistry(MODELS_DIR)
    entries = {key: registry.entry(mid) for key, mid in MODEL_IDS.items()}
    entries["alert"] = {**entries["alert"], **registry.data["alert_threshold"], "id": MODEL_IDS["alert"]}
    scores = df[["address", "in_random", "half_a", "listed_sources", "elliptic_class", "anchor_block", "anchor_time",
                 "total_txs",
                 "window_truncated", "illicit_hop_dist", "illicit_within_2", "illicit_nearest_seed"]].copy()
    for key, entry in entries.items():
        scores[f"score_{key}"] = registry.model(entry["id"]).predict_proba(df[entry["features"]])[:, 1]
    found = thresholds(scores, entries)
    for key, entry in entries.items():
        new = found[entry["id"]]["new"]
        scores[f"flag_{key}_new"] = scores[f"score_{key}"] >= new
        old = entry["recent_threshold"] if key == "alert" else entry["threshold"]
        scores[f"flag_{key}_old"] = scores[f"score_{key}"] >= old
        mask = scores[f"flag_{key}_new"].to_numpy()
        scores[f"shap_{key}"] = None
        if key != "rf_v2_exp":  # SHAP on the XGBoost models only (forest SHAP is slow)
            scores.loc[mask, f"shap_{key}"] = top_shap(registry.model(entry["id"]), df.loc[mask, entry["features"]])
    timings["scoring_and_shap"] = round(time.time() - t)
    scores.to_parquet(OUT / "scores.parquet", index=False)

    pulls = manifest.get("pulls", [])
    import csv

    from training.bq_common import COST_LOG
    with COST_LOG.open(encoding="utf-8") as handle:  # every recent-store query, from the cost log
        billed = sum(float(r["billed_gb"]) for r in csv.DictReader(handle)
                     if r["label"].startswith(("recent_blocks", "recent_vsize", "recent_tip", "recent_transactions")))
    rand = scores[scores.in_random]
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "store": {"tip": tip, "tip_time": manifest.get("tip_time"), "blocks": [need_lo, tip],
                  "active_week_blocks": [tip - WEEK_BLOCKS + 1, tip], "files": files, "covered": covered,
                  "billed_gb_recent_store_total": round(billed, 3),
                  "pulls": pulls},
        "counts": {"active_addresses_newest_week": int(len(active)), "scored": int(len(scores)),
                   "random_scored": int(len(rand)), "random_half_a": int(rand.half_a.sum()),
                   "listed_scored": int(scores.listed_sources.notna().sum()),
                   "listed_by_source": {s: int(scores.listed_sources.fillna("").str.contains(s).sum())
                                        for s in ("ofac", "ransomwhere", "scam")},
                   "elliptic_scored": {"illicit": int((scores.elliptic_class == 1).sum()),
                                       "licit": int((scores.elliptic_class == 2).sum())},
                   "kept_pairs": int(len(kept)), "kept_transactions": int(graph_stats["transactions"]),
                   "window_truncated_share": round(float(scores.window_truncated.mean()), 4)},
        "sample_rule": f"random sample of {args.sample:,} (seed 0) of addresses active in the newest week, "
                       "plus every active listed or Elliptic++-labelled address (kept apart)",
        "graph": graph_stats,
        "hop_distance_random": rand.illicit_hop_dist.value_counts().sort_index().astype(int).to_dict(),
        "thresholds": found,
        "score_quantiles_random": {key: {str(q): round(float(rand[f"score_{key}"].quantile(q)), 4)
                                         for q in (0.5, 0.9, 0.99, 0.999)} for key in entries},
        "timings_seconds": {**timings, "total": round(time.time() - started)},
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    log(f"saved {OUT}; thresholds: " + json.dumps({k: v["new"] for k, v in found.items()}))
    if not args.no_registry and args.span == SPAN_BLOCKS and not args.tip:
        update_registry(summary)


if __name__ == "__main__":
    main()
