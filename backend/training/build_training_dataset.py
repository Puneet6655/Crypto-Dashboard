"""Merge the BigQuery wallet features with the Elliptic++ labels into one training table.

Run after training.pull_bigquery, from backend/:
    python -m training.build_training_dataset

Reads data/processed/bigquery/*.parquet and the Elliptic++ CSVs, writes
data/processed/training_dataset.parquet and reports/bigquery_training_dataset.md.

Every row is one (address, time step): the address was active in that step's snapshot, and
its features describe the four weeks of on-chain activity ending at its newest transaction
in the snapshot. The same features can be computed live (app/features/wallet_window.py).

source:
  elliptic_pp  the (address, step) is an Elliptic++ wallet row. label: 1 illicit, 0 licit,
               empty for Elliptic++ "unknown". Only these rows have labels.
  bq_same_era  active in an Elliptic++ snapshot (steps 1-49) but not in Elliptic++. No label.
  bq_post      active in a continuation snapshot (steps 50-101, about two years after). No label.
known_elliptic_class is the address's Elliptic++ class wherever it appears, for drift studies
on bq_post rows. It is not a label for that row: the address may have changed hands or use.
"""
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import pyarrow.parquet as pq

from app.features.wallet_window import (
    ADDRESS_FEATURE_NAMES, EXPOSURE_FEATURE_NAMES, FEATURE_NAMES, NETWORK_BLOCKS, NETWORK_FEATURE_NAMES,
    SATS_PER_BTC, address_type_features,
)
from training import bq_common, exposure
from training.bq_common import BACKEND_DIR, COST_LOG, OUT_DIR
from training.time_steps import LAST_ELLIPTIC_STEP

ELLIPTIC_DIR = BACKEND_DIR / "data" / "raw" / "elliptic_pp"
DATASET = BACKEND_DIR / "data" / "processed" / "training_dataset.parquet"
REPORT = BACKEND_DIR / "reports" / "bigquery_training_dataset.md"
EXTERNAL_LABELS = BACKEND_DIR / "data" / "raw" / "indicators" / "bitcoin_labels.csv"
FINGERPRINTS = OUT_DIR / "label_fingerprints.parquet"
TRAIN_LAST_STEP = 34  # Elliptic++ paper split: steps 1-34 train, 35-49 test


def add_network_context(df: pd.DataFrame, blocks: pd.DataFrame) -> pd.DataFrame:
    """net_tx_count_144, net_avg_fee_btc_144, fee_vs_network_mean for each row's anchor block."""
    blocks = blocks.sort_values("block_number")
    lo, hi = int(blocks.block_number.min()), int(blocks.block_number.max())
    if len(blocks) != hi - lo + 1:
        raise ValueError(f"block_stats has gaps: {len(blocks)} rows for blocks {lo}-{hi}")
    # Cumulative sums with a leading zero: total over [a - 143, a] = cum[a - lo + 1] - cum[a - lo - 143].
    cum_tx = np.concatenate([[0], np.cumsum(blocks.tx_count.to_numpy(np.int64))])
    cum_fee = np.concatenate([[0], np.cumsum(blocks.total_fees_sats.to_numpy(np.int64))])
    end = df.anchor_block.to_numpy(np.int64) - lo + 1
    start = end - NETWORK_BLOCKS
    if start.min() < 0:
        raise ValueError("block_stats does not reach 144 blocks before the earliest anchor")
    tx_count = cum_tx[end] - cum_tx[start]
    fees = cum_fee[end] - cum_fee[start]
    avg_fee = fees / (tx_count - NETWORK_BLOCKS) / SATS_PER_BTC  # minus one coinbase per block
    df["net_tx_count_144"] = tx_count.astype("float64")
    df["net_avg_fee_btc_144"] = avg_fee
    df["fee_vs_network_mean"] = np.where(avg_fee > 0, df["fees_mean"] / np.where(avg_fee > 0, avg_fee, 1), 0.0)
    return df


def load_elliptic() -> tuple[pd.DataFrame, pd.Series]:
    """(Elliptic++ wallet rows as address/step, class per address)."""
    rows = pd.read_csv(ELLIPTIC_DIR / "wallets_features.csv", usecols=["address", "Time step"], dtype={"address": "string"})
    rows = rows.rename(columns={"Time step": "step"}).drop_duplicates()
    classes = pd.read_csv(ELLIPTIC_DIR / "wallets_classes.csv", dtype={"address": "string", "class": "int8"})
    return rows, classes.drop_duplicates("address").set_index("address")["class"]


def load_external() -> pd.DataFrame | None:
    """OFAC / Ransomwhere rows (address, source, label, family, first_seen, ...), if downloaded."""
    if not EXTERNAL_LABELS.exists():
        return None
    return pd.read_csv(EXTERNAL_LABELS, dtype=str)


def label_fingerprints(addresses: list[str]) -> dict[str, int]:
    """FARM_FINGERPRINT per label address, cached in label_fingerprints.parquet."""
    cached = pd.read_parquet(FINGERPRINTS) if FINGERPRINTS.exists() else pd.DataFrame(columns=["address", "fp"])
    known = dict(zip(cached["address"], cached["fp"].astype("int64")))
    missing = sorted(set(addresses) - set(known))
    if missing:
        known.update(exposure.fingerprints(bq_common.get_client(), missing))
        pd.DataFrame({"address": list(known), "fp": list(known.values())}).to_parquet(FINGERPRINTS, index=False)
    return known


def build() -> tuple[pd.DataFrame, dict]:
    features_path = OUT_DIR / "wallet_window_features.parquet"
    columns = [c for c in pq.read_schema(features_path).names if c not in ("payer_fps", "payee_fps")]
    features = pd.read_parquet(features_path, columns=columns)
    features["address"] = features["address"].astype("string")
    float_cols = [c for c in FEATURE_NAMES if c in features.columns]
    features[float_cols] = features[float_cols].astype("float32")  # halves memory; plenty for training
    blocks = pd.read_parquet(OUT_DIR / "block_stats.parquet")
    steps = pd.read_csv(OUT_DIR / "time_steps.csv", parse_dates=["snap_start_time", "snap_end_time"])
    elliptic_rows, elliptic_class = load_elliptic()

    df = features.merge(steps[["step", "period", "snap_end_time"]], on="step", how="left")
    times = blocks.set_index("block_number")["block_timestamp"]
    df["anchor_time"] = df["anchor_block"].map(times)

    in_elliptic = df.merge(elliptic_rows.assign(_hit=True), on=["address", "step"], how="left")["_hit"].fillna(False).to_numpy()
    df["source"] = np.where(in_elliptic, "elliptic_pp", np.where(df.step <= LAST_ELLIPTIC_STEP, "bq_same_era", "bq_post"))
    df["known_elliptic_class"] = df["address"].map(elliptic_class).astype("Int8")
    df["elliptic_class"] = df["known_elliptic_class"].where(in_elliptic).astype("Int8")
    df["label"] = df["elliptic_class"].map({1: 1, 2: 0}).astype("Int8")  # 3 (unknown) stays empty
    df["split"] = np.where(df.step <= TRAIN_LAST_STEP, "train", np.where(df.step <= LAST_ELLIPTIC_STEP, "test", "post"))
    df = add_network_context(df, blocks)

    address_types = pd.DataFrame([address_type_features(a) for a in df["address"].astype(str)], dtype="float32")
    df[list(ADDRESS_FEATURE_NAMES)] = address_types.to_numpy()

    external = load_external()
    if external is not None:
        grouped = external.groupby("address").agg(
            external_label=("label", lambda s: "|".join(sorted(set(s)))),
            external_family=("family", lambda s: "|".join(sorted(set(s.dropna())))),
            external_first_seen=("first_seen", "min"), external_last_seen=("last_seen", "max"))
        df = df.join(grouped, on="address")
    else:
        df["external_label"] = df["external_family"] = df["external_first_seen"] = df["external_last_seen"] = None

    if "payer_fps" in pq.read_schema(features_path).names:
        labels = exposure.label_table(elliptic_rows, elliptic_class, external, steps)
        exposure_cols = exposure.exposure_frame(features_path, labels, label_fingerprints(labels["address"].tolist()))
        df[list(EXPOSURE_FEATURE_NAMES)] = exposure_cols.astype("float32").to_numpy()
    else:
        for name in EXPOSURE_FEATURE_NAMES:
            df[name] = np.float32(0)

    id_cols = ["address", "step", "period", "split", "source", "label", "elliptic_class", "known_elliptic_class",
               "external_label", "external_family", "external_first_seen", "external_last_seen",
               "snap_end", "snap_end_time", "anchor_block", "anchor_time", "n_in_window"]
    df = df[id_cols + list(FEATURE_NAMES) + list(ADDRESS_FEATURE_NAMES) + list(NETWORK_FEATURE_NAMES)
            + list(EXPOSURE_FEATURE_NAMES)]

    matched = elliptic_rows.merge(df.loc[in_elliptic, ["address", "step"]], on=["address", "step"], how="left", indicator=True)
    facts = {
        "elliptic_rows": len(elliptic_rows),
        "elliptic_rows_found": int((matched["_merge"] == "both").sum()),
        "missing_examples": matched.loc[matched["_merge"] == "left_only", "address"].head(5).tolist(),
        "steps": steps,
        "blocks": (int(blocks.block_number.min()), int(blocks.block_number.max())),
    }
    return df, facts


def cost_summary() -> str:
    if not COST_LOG.exists():
        return "No cost log found."
    log = pd.read_csv(COST_LOG)
    lines = ["| Query | Estimated GB | Billed GB |", "| --- | --- | --- |"]
    lines += [f"| {r.label} | {r.estimated_gb:.2f} | {r.billed_gb:.2f} |" for r in log.itertuples()]
    lines.append(f"| **Total** | | **{log.billed_gb.sum():.1f}** |")
    return "\n".join(lines)


def write_report(df: pd.DataFrame, facts: dict) -> None:
    steps = facts["steps"]
    counts = df.groupby("source").size()
    labelled = df[df.label.notna()]
    by_split = labelled.groupby(["split", "label"]).size().unstack(fill_value=0)
    e = steps[steps.period == "elliptic"]
    p = steps[steps.period == "post"]

    def day(ts) -> str:
        return pd.Timestamp(ts).strftime("%Y-%m-%d %H:%M")

    step_rows = "\n".join(
        f"| {r.step} | {r.period} | {r.snap_start:,}-{r.snap_end:,} | {day(r.snap_start_time)} | {day(r.snap_end_time)} |"
        for r in steps.itertuples()
    )
    lines = [
        "# BigQuery + Elliptic++ training dataset",
        "",
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by training/build_training_dataset.py.",
        "File: `data/processed/training_dataset.parquet` (gitignored; rebuild with the commands below).",
        "",
        "## What a row is",
        "",
        "One (address, time step). The address was active in that step's snapshot. Its features describe its",
        "on-chain activity in the 4,032 blocks (about four weeks) ending at its newest transaction in the snapshot,",
        "using at most its 100 newest transactions. `app/features/wallet_window.py` computes the same features",
        "from live mempool.space data, so a model trained on this table can score live lookups.",
        "",
        "## Time steps and dates",
        "",
        "Elliptic++ has no dates, only time steps 1-49. Its wallet file has real block heights, and they show",
        "that step t is a snapshot of 6-16 blocks (under three hours) starting at block 391,200 + 2016 x (t - 1).",
        "2016 blocks is one difficulty period, about two weeks. Block times come from BigQuery.",
        f"Elliptic++ therefore covers {day(e.snap_start_time.min())} to {day(e.snap_end_time.max())} UTC.",
        f"Steps 50-101 continue the same schedule (9-block snapshots) for two years: {day(p.snap_start_time.min())}"
        f" to {day(p.snap_end_time.max())} UTC.",
        "The full table is in `data/processed/bigquery/time_steps.csv` and at the end of this report.",
        "",
        "## Rows and labels",
        "",
        f"- Rows: {len(df):,} ({', '.join(f'{k}: {v:,}' for k, v in counts.items())})",
        f"- Elliptic++ wallet rows found in BigQuery: {facts['elliptic_rows_found']:,} of {facts['elliptic_rows']:,}"
        f" ({facts['elliptic_rows_found'] / facts['elliptic_rows']:.2%})",
        "- Labels exist only on `elliptic_pp` rows: 1 illicit, 0 licit, empty for Elliptic++ class 3 (unknown).",
        "  Unknown is not licit; do not use it as a negative.",
        "- `external_label` (ransomware / sanctioned), `external_family`, `external_first_seen/_last_seen`: the",
        "  address is on the Ransomwhere or OFAC list (data/raw/indicators/bitcoin_labels.csv). Not a training label",
        "  by default; used to check models on data Elliptic++ never saw.",
        "- `known_elliptic_class` repeats an address's Elliptic++ class on any row (including `bq_post`). It is",
        "  for drift studies, not a label: an address can change owner or use over two years.",
        "- `split`: train = steps 1-34, test = 35-49 (the Elliptic++ paper's split), post = 50-101. Some",
        "  addresses appear in both train and test steps; split by address too when evaluating.",
        "",
        "Labelled rows by split:",
        "",
        "| Split | Licit (0) | Illicit (1) |",
        "| --- | --- | --- |",
    ]
    lines += [f"| {split} | {row.get(0, 0):,} | {row.get(1, 0):,} |" for split, row in by_split.iterrows()]
    lines += [
        "",
        "## Features",
        "",
        "Names follow Elliptic++ where the idea is the same, but the values are recomputed from all on-chain",
        "transactions, not copied from the Elliptic++ CSV (which counts only transactions inside its sample).",
        "Amounts are BTC; a statistic over an empty list is 0. Block heights are relative to the anchor, so",
        "no feature encodes the era directly.",
        "",
        "- Counts: total_txs, num_txs_as_sender, num_txs_as_receiver, num_active_days, window_truncated",
        "- Timing: lifetime_in_blocks, blocks_since_first_sent / _received (-1 if none), blocks_btwn_txs_*,",
        "  blocks_btwn_input_txs_*, blocks_btwn_output_txs_*",
        "- Amounts: btc_transacted_*, btc_sent_*, btc_received_*, fees_*, fees_as_share_* (fee / all input value)",
        "- Counterparties: num_counterparties, num_addr_transacted_multiple, transacted_w_address_* (transactions",
        "  per counterparty; counterparties = who it paid when sending, who paid it when receiving)",
        "- Network context (144 blocks ending at the anchor): net_tx_count_144, net_avg_fee_btc_144,",
        "  fee_vs_network_mean (the wallet's mean fee over the network's). Live: mempool.space block list.",
        "  The first two mostly identify the date, so a model can learn 'when' instead of 'what';",
        "  training/baseline_model.py leaves them out by default (see reports/baseline_model.md).",
        "- Transaction shape: fee_rate_* (sat/vB), tx_inputs_*, tx_outputs_*, round_received_share,",
        "  round_payment_share (round = a multiple of 0.001 BTC), self_change_share",
        "- Last week of the window: total_txs_1w, btc_received_total_1w, btc_sent_total_1w, num_counterparties_1w",
        "- Address type from the address text: addr_p2pkh, addr_p2sh, addr_bech32",
        "- Exposure (training/exposure.py): illicit_/licit_ payers and payees, as counts and shares of all payers",
        "  or payees. Illicit = Elliptic++ class 1 + Ransomwhere; licit = Elliptic++ class 2. A label counts only",
        "  if it was known before the row's step. OFAC has no dates, so it is used for live lookups only.",
        "- Each `_*` family has _total, _min, _max, _mean, _median.",
        "",
        "Not model features: address, step, period, split, source, labels, snap_end(_time), anchor_block/_time,",
        "n_in_window (transactions in the window before the 100 cap).",
        "",
        "## Checks",
        "",
        "- `python -m training.check_sql_parity`: the SQL and the Python function agree on every value",
        "  (359 wallets x 56 values on step 25, including capped and counterparty-heavy wallets).",
        "- 77 transactions from block 500,000 covering every script type came out identical from BigQuery and",
        "  mempool.space after conversion.",
        "- `python -m training.check_live_parity`: recomputes sample rows from live mempool.space data. On",
        "  2026-10-30, 25 of 25 rows (labelled, same-era and post) matched, including 3 network-context checks.",
        "- `daily_network_stats.csv`: the first and last day are partial (see its `blocks` column).",
        "",
        "## Known differences from live data",
        "",
        "- Live scoring anchors at the address's newest transaction now; training anchors at the snapshot.",
        "  An address with no transactions returns all-zero features: do not score it.",
        "- When more than 100 transactions share the window edge, BigQuery and mempool.space may keep a",
        "  different transaction from the same block. Only busy wallets (window_truncated = 1) are affected.",
        "- Era: 2015-2019 behaviour, fee levels and address types differ from today (see",
        "  training/FEATURE_MISMATCH.md). Use the bq_post rows to measure drift before trusting scores.",
        "",
        "## BigQuery cost",
        "",
        "On-demand pricing bills bytes scanned; the first 1 TB per month is free. Every query was dry-run first",
        "and sent with a byte cap. Billed so far by these scripts:",
        "",
        cost_summary(),
        "",
        "## Rebuild",
        "",
        "```",
        "cd backend",
        "..\\.venv\\Scripts\\python.exe -m pip install -r requirements-ml.txt",
        "..\\.venv\\Scripts\\python.exe -m training.pull_bigquery plan",
        "..\\.venv\\Scripts\\python.exe -m training.pull_bigquery run",
        "..\\.venv\\Scripts\\python.exe -m training.build_training_dataset",
        "```",
        "",
        "## Time step table",
        "",
        "| Step | Period | Blocks | Start (UTC) | End (UTC) |",
        "| --- | --- | --- | --- | --- |",
        step_rows,
        "",
    ]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    df, facts = build()
    DATASET.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(DATASET, index=False)
    print(f"saved {len(df):,} rows to {DATASET}")
    print(df.groupby(["source", "label"], dropna=False).size().to_string())
    write_report(df, facts)
    print(f"report written to {REPORT}")


if __name__ == "__main__":
    main()
