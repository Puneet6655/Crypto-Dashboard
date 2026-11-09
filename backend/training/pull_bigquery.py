"""Pull Bitcoin data from BigQuery for the Elliptic++ period and the two years after it.

Run from backend/ with the project's Python environment:
    python -m training.pull_bigquery plan   # dry runs: prints what each query would scan (costs one ~10 MB lookup)
    python -m training.pull_bigquery run    # runs the queries with hard byte limits, then downloads

Three queries, each reading the public table once:
  1. block_stats      per-block tx count and fees (the daily query you started from, kept per block)
  2. active_addresses addresses active in each time-step snapshot (steps 1-49 all, 50-101 sampled)
  3. wallet_features  window features for every (address, step) from query 2

Outputs (data/processed/bigquery/, gitignored):
  block_stats.parquet, daily_network_stats.csv, time_steps.csv, wallet_window_features.parquet,
  jobs.json (job ids, so a rerun within 24 hours reuses results instead of paying again),
  cost_log.csv (estimate and billed GB of every query).
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from google.api_core.exceptions import NotFound
from google.cloud import bigquery

from app.features.wallet_window import (
    MAX_TXS, NETWORK_BLOCKS, ROUND_SATS, STAT_FAMILIES, STAT_SUFFIXES, WEEK_BLOCKS, WINDOW_BLOCKS,
)
from training import bq_common
from training.time_steps import BASE_BLOCK, LAST_ELLIPTIC_STEP, STEP_BLOCKS, attach_dates, steps_frame

SQL_DIR = Path(__file__).resolve().parent / "sql"
OUT = bq_common.OUT_DIR
JOBS_FILE = OUT / "jobs.json"
# Script types that have one standard address; mempool.space shows an address for exactly these.
STD_TYPES = ["pubkeyhash", "scripthash", "witness_v0_keyhash", "witness_v0_scripthash", "witness_v1_taproot"]
PLAN_STANDIN = "(SELECT 'x' AS address, 1 AS step, 1 AS snap_end)"
# Bump when the SQL changes, so results of an older query version are never reused.
QUERY_VERSION = "v2"
ELLIPTIC_CLASSES = bq_common.BACKEND_DIR / "data" / "raw" / "elliptic_pp" / "wallets_classes.csv"
MAX_PARAM_MB = 8.0  # BigQuery rejects requests over 10 MB


def load_jobs() -> dict:
    return json.loads(JOBS_FILE.read_text()) if JOBS_FILE.exists() else {}


def save_job(name: str, job: bigquery.QueryJob) -> None:
    jobs = load_jobs()
    jobs[name] = {"job_id": job.job_id, "location": job.location,
                  "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    OUT.mkdir(parents=True, exist_ok=True)
    JOBS_FILE.write_text(json.dumps(jobs, indent=2))


def reusable_job(client: bigquery.Client, name: str) -> bigquery.QueryJob | None:
    """A finished job from an earlier run whose result table still exists (about 24 hours)."""
    saved = load_jobs().get(name)
    if not saved:
        return None
    try:
        job = client.get_job(saved["job_id"], location=saved["location"])
        client.get_table(job.destination)
        print(f"[{name}] reusing result of job {job.job_id} (no new cost)")
        return job
    except (NotFound, AttributeError, TypeError):
        return None


def month_bounds(client: bigquery.Client, block_lo: int, block_hi: int) -> tuple[str, str]:
    """First-of-month dates covering the two blocks (tiny query on the blocks view)."""
    sql = """SELECT MIN(timestamp_month) AS lo, MAX(timestamp_month) AS hi
             FROM `bigquery-public-data.crypto_bitcoin.blocks` WHERE number IN (@lo, @hi)"""
    params = [bigquery.ScalarQueryParameter("lo", "INT64", block_lo), bigquery.ScalarQueryParameter("hi", "INT64", block_hi)]
    row = list(bq_common.run_query(client, sql, params, label="month_bounds", max_gb=0.5).result())[0]
    return row["lo"].isoformat(), row["hi"].isoformat()


def stat_columns_sql() -> str:
    return ",\n  ".join(
        f"s_{family}.{suffix} AS {family}_{suffix}" for family in STAT_FAMILIES for suffix in STAT_SUFFIXES
    )


def steps_param(steps: pd.DataFrame) -> bigquery.ArrayQueryParameter:
    structs = [
        bigquery.StructQueryParameter(
            None,
            bigquery.ScalarQueryParameter("step", "INT64", int(r.step)),
            bigquery.ScalarQueryParameter("snap_start", "INT64", int(r.snap_start)),
            bigquery.ScalarQueryParameter("snap_end", "INT64", int(r.snap_end)),
        )
        for r in steps.itertuples()
    ]
    return bigquery.ArrayQueryParameter("steps", "STRUCT", structs)


def build_queries(steps: pd.DataFrame, months: tuple[str, str], post_sample: int, active_table: str,
                  window_blocks: int = WINDOW_BLOCKS, max_txs: int = MAX_TXS,
                  extra_all: list[str] | None = None, extra_post: list[str] | None = None,
                  week_blocks: int = WEEK_BLOCKS) -> dict:
    """Return {name: (sql, params)} for the three queries. window_blocks/max_txs change only for tests.

    extra_all: labelled addresses observed at every step (e.g. OFAC, ransomware lists).
    extra_post: labelled addresses observed at steps 50-101 only (Elliptic++ addresses after 2017).
    """
    block_hi = int(steps.snap_end.max())
    legs_lo = int(steps.snap_end.min()) - window_blocks + 1
    stats_lo = legs_lo - NETWORK_BLOCKS + 1
    month_params = [bigquery.ScalarQueryParameter("month_lo", "DATE", months[0]),
                    bigquery.ScalarQueryParameter("month_hi", "DATE", months[1])]
    types = bigquery.ArrayQueryParameter("std_types", "STRING", STD_TYPES)

    snapshot_filter = " OR ".join(
        f"block_number BETWEEN {int(r.snap_start)} AND {int(r.snap_end)}" for r in steps.itertuples()
    )
    active_sql = (SQL_DIR / "active_addresses.sql").read_text().replace("{snapshot_filter}", snapshot_filter)
    features_sql = ((SQL_DIR / "wallet_window_features.sql").read_text()
                    .replace("{active_table}", active_table)
                    .replace("{stat_columns}", stat_columns_sql()))
    return {
        "block_stats": ((SQL_DIR / "block_stats.sql").read_text(), month_params + [
            bigquery.ScalarQueryParameter("block_lo", "INT64", stats_lo),
            bigquery.ScalarQueryParameter("block_hi", "INT64", block_hi)]),
        "active_addresses": (active_sql, month_params + [
            steps_param(steps), types,
            bigquery.ScalarQueryParameter("base_block", "INT64", BASE_BLOCK),
            bigquery.ScalarQueryParameter("step_blocks", "INT64", STEP_BLOCKS),
            bigquery.ScalarQueryParameter("post_first_step", "INT64", LAST_ELLIPTIC_STEP + 1),
            bigquery.ScalarQueryParameter("post_sample", "INT64", post_sample),
            bigquery.ArrayQueryParameter("extra_all", "STRING", sorted(set(extra_all or []))),
            bigquery.ArrayQueryParameter("extra_post", "STRING", sorted(set(extra_post or [])))]),
        "wallet_features": (features_sql, month_params + [
            types,
            bigquery.ScalarQueryParameter("block_lo", "INT64", legs_lo),
            bigquery.ScalarQueryParameter("block_hi", "INT64", block_hi),
            bigquery.ScalarQueryParameter("window_blocks", "INT64", window_blocks),
            bigquery.ScalarQueryParameter("week_blocks", "INT64", week_blocks),
            bigquery.ScalarQueryParameter("round_sats", "INT64", ROUND_SATS),
            bigquery.ScalarQueryParameter("max_txs", "INT64", max_txs)]),
    }


def read_label_addresses(paths: list[Path]) -> list[str]:
    """Addresses from label CSVs (an 'address' column; other columns are ignored here)."""
    found: set[str] = set()
    for path in paths:
        table = pd.read_csv(path, dtype=str)
        if "address" not in table.columns:
            raise ValueError(f"{path} has no 'address' column (columns: {list(table.columns)})")
        found.update(a.strip() for a in table["address"].dropna() if a.strip())
    return sorted(found)


def elliptic_post_addresses(licit_sample: int) -> list[str]:
    """Every Elliptic++ illicit address plus a fixed sample of licit ones, to follow after 2017."""
    classes = pd.read_csv(ELLIPTIC_CLASSES, dtype={"address": str, "class": int})
    illicit = classes.loc[classes["class"] == 1, "address"]
    licit = classes.loc[classes["class"] == 2, "address"].sample(licit_sample, random_state=0)
    return sorted(set(illicit) | set(licit))


def write_block_outputs(block_stats: pd.DataFrame, steps: pd.DataFrame) -> None:
    """daily_network_stats.csv (the original daily query) and time_steps.csv (step -> dates)."""
    blocks = block_stats.sort_values("block_number")
    day = pd.to_datetime(blocks["block_timestamp"], utc=True).dt.date
    daily = blocks.assign(date=day).groupby("date").agg(
        total_transactions=("tx_count", "sum"), blocks=("block_number", "count"), total_fees_sats=("total_fees_sats", "sum"))
    non_coinbase = daily["total_transactions"] - daily["blocks"]
    daily["total_fees_btc"] = daily["total_fees_sats"] / 1e8
    daily["avg_fee_btc"] = daily["total_fees_btc"] / non_coinbase
    daily.drop(columns="total_fees_sats").to_csv(OUT / "daily_network_stats.csv")
    dated = attach_dates(steps, blocks[["block_number", "block_timestamp"]])
    dated.to_csv(OUT / "time_steps.csv", index=False)
    print(f"saved {len(daily):,} days to daily_network_stats.csv and {len(dated)} steps to time_steps.csv")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=["plan", "run"])
    parser.add_argument("--post-sample", type=int, default=25_000,
                        help="addresses kept per continuation step (50-101); default 25,000")
    parser.add_argument("--max-total-gb", type=float, default=400.0,
                        help="refuse to start if the three estimates add up to more than this")
    parser.add_argument("--labels", type=Path, nargs="*", default=[],
                        help="CSV files with an 'address' column (OFAC, ransomware...) to follow at every step")
    parser.add_argument("--elliptic-licit-sample", type=int, default=50_000,
                        help="licit Elliptic++ addresses to follow after 2017, with all illicit ones (0 = none)")
    args = parser.parse_args()
    extra_all = read_label_addresses(args.labels)
    extra_post = elliptic_post_addresses(args.elliptic_licit_sample) if args.elliptic_licit_sample else []
    param_mb = (sum(len(a) + 4 for a in extra_all) + sum(len(a) + 4 for a in extra_post)) / 1e6
    print(f"extra addresses: {len(extra_all):,} at every step, {len(extra_post):,} after 2017 (~{param_mb:.1f} MB)")
    if param_mb > MAX_PARAM_MB:
        raise ValueError(f"extra address lists are {param_mb:.1f} MB; BigQuery requests must stay under 10 MB")

    client = bq_common.get_client()
    steps = steps_frame()
    block_hi = int(steps.snap_end.max())
    stats_lo = int(steps.snap_end.min()) - WINDOW_BLOCKS + 1 - NETWORK_BLOCKS + 1
    months = month_bounds(client, stats_lo, block_hi)
    print(f"blocks {stats_lo:,} to {block_hi:,}, partitions {months[0]} to {months[1]}")

    plan = build_queries(steps, months, args.post_sample, PLAN_STANDIN, extra_all=extra_all, extra_post=extra_post)
    estimates = {name: bq_common.estimate_gb(client, sql, params) for name, (sql, params) in plan.items()}
    for name, gb in estimates.items():
        print(f"  {name:18s} {gb:8.1f} GB")
    total = sum(estimates.values())
    already = bq_common.billed_this_month_gb()
    print(f"  {'total':18s} {total:8.1f} GB  (these scripts already billed {already:.1f} GB this month;"
          f" free tier is {bq_common.FREE_TIER_GB_PER_MONTH} GB per month for the whole project)")
    if args.mode == "plan":
        return
    if total > args.max_total_gb:
        raise bq_common.CostLimitError(f"Plan needs {total:.0f} GB, above --max-total-gb {args.max_total_gb:.0f}")

    def run(name: str, sql: str, params: list) -> bigquery.QueryJob:
        versioned = name if name == "block_stats" else f"{name}_{QUERY_VERSION}"
        job = reusable_job(client, versioned)
        if job is None:
            # 10% headroom over the dry-run estimate, plus 1 GB for small queries.
            job = bq_common.run_query(client, sql, params, label=versioned, max_gb=estimates[name] * 1.1 + 1)
            save_job(versioned, job)
        return job

    if (OUT / "block_stats.parquet").exists():
        print("[block_stats] already downloaded; delete block_stats.parquet to pull it again")
    else:
        job = run("block_stats", *plan["block_stats"])
        table = bq_common.download(client, job, OUT / "block_stats.parquet")
        write_block_outputs(table.to_pandas(), steps)

    active_job = run("active_addresses", *plan["active_addresses"])
    print(f"active (address, step) rows: {active_job.result().total_rows:,}")
    active_table = f"`{bq_common.result_table_id(active_job)}`"
    real = build_queries(steps, months, args.post_sample, active_table, extra_all=extra_all, extra_post=extra_post)
    job = run("wallet_features", *real["wallet_features"])
    bq_common.download(client, job, OUT / "wallet_window_features.parquet")
    print(f"billed this month by these scripts: {bq_common.billed_this_month_gb():.1f} GB")


if __name__ == "__main__":
    main()
