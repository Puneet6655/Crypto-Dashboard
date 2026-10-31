"""Save the BigQuery transactions behind every training row, for graph (hop) features and alerts.

Run from backend/ with the root .venv:
    python -m training.transaction_store plan            # dry runs only: GB per chunk and total
    python -m training.transaction_store run             # runs (refuses above --max-total-gb), downloads

A training row is (address, step). It uses the 100 newest transactions touching the address in
the 4,032 blocks ending at the step's snapshot (the same rule as the wallet features). This saves
those transactions with every input and output (address + value), so graphs can be built locally.

Output (gitignored): data/raw/bigquery/transactions/step=NNN/part-0.parquet, one row per
(step, transaction): step, tx_hash, block_height, block_time, is_coinbase, fee_sats,
inputs / outputs (list of {address, value} in sats, address NULL when not one standard address),
row_addresses (the training-row addresses this transaction was kept for).
A transaction used by two steps (windows overlap by about two weeks) is stored in both.

The active-address table comes from the active_addresses_v2 job (copied to active_addresses_v2_copy
on 2026-10-31; BigQuery keeps job results about 24 hours). Steps are queried in chunks so each
result stays under BigQuery's 10 GB response limit; each chunk reads only its own block range.
"""
import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from google.cloud import bigquery

from app.features.wallet_window import MAX_TXS, WINDOW_BLOCKS
from training import bq_common
from training.pull_bigquery import STD_TYPES, SQL_DIR, load_jobs, month_bounds, reusable_job, save_job
from training.time_steps import LAST_STEP, steps_frame

STORE = bq_common.BACKEND_DIR / "data" / "raw" / "bigquery" / "transactions"
MANIFEST = STORE / "manifest.json"
ACTIVE_JOB = "active_addresses_v2_copy"
QUERY_VERSION = "v1"


def chunks(steps_per_chunk: int) -> list[tuple[int, int]]:
    return [(lo, min(lo + steps_per_chunk - 1, LAST_STEP)) for lo in range(1, LAST_STEP + 1, steps_per_chunk)]


def chunk_query(client, steps, lo: int, hi: int, active_table: str, months_cache: dict) -> tuple[str, list]:
    part = steps[steps.step.between(lo, hi)]
    block_lo = int(part.snap_end.min()) - WINDOW_BLOCKS + 1
    block_hi = int(part.snap_end.max())
    if (block_lo, block_hi) not in months_cache:
        months_cache[(block_lo, block_hi)] = month_bounds(client, block_lo, block_hi)
    month_lo, month_hi = months_cache[(block_lo, block_hi)]
    sql = (SQL_DIR / "window_transactions.sql").read_text().replace("{active_table}", active_table)
    params = [
        bigquery.ScalarQueryParameter("month_lo", "DATE", month_lo),
        bigquery.ScalarQueryParameter("month_hi", "DATE", month_hi),
        bigquery.ScalarQueryParameter("block_lo", "INT64", block_lo),
        bigquery.ScalarQueryParameter("block_hi", "INT64", block_hi),
        bigquery.ScalarQueryParameter("step_lo", "INT64", lo),
        bigquery.ScalarQueryParameter("step_hi", "INT64", hi),
        bigquery.ArrayQueryParameter("std_types", "STRING", STD_TYPES),
        bigquery.ScalarQueryParameter("window_blocks", "INT64", WINDOW_BLOCKS),
        bigquery.ScalarQueryParameter("max_txs", "INT64", MAX_TXS),
    ]
    return sql, params


def load_manifest() -> dict:
    return json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}


def download_by_step(client, job, lo: int, hi: int, workers: int, chunk_rows: int = 100_000) -> dict[int, int]:
    """Read a chunk's result table through the free REST API and write one Parquet file per step."""
    table = client.get_table(job.destination)
    total = table.num_rows
    starts = list(range(0, total, chunk_rows)) or [0]
    for step in range(lo, hi + 1):  # a rerun starts the chunk's steps from scratch
        (STORE / f"step={step:03d}" / "part-0.parquet.partial").unlink(missing_ok=True)

    def fetch(start: int) -> pa.Table:
        for attempt in range(5):  # retry network errors (an SSL drop killed one download)
            try:
                return bq_common.get_client().list_rows(table, start_index=start, max_results=chunk_rows,
                                                        page_size=25_000).to_arrow(create_bqstorage_client=False)
            except Exception as exc:  # noqa: BLE001
                if attempt == 4:
                    raise
                print(f"  page at row {start:,} failed ({type(exc).__name__}); retrying", flush=True)
                time.sleep(10 * 2 ** attempt)

    writers: dict[int, pq.ParquetWriter] = {}
    counts: dict[int, int] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, part in enumerate(pool.map(fetch, starts), start=1):
            for step in pc.unique(part.column("step")).to_pylist():
                rows = part.filter(pc.equal(part.column("step"), step))
                if step not in writers:
                    folder = STORE / f"step={step:03d}"
                    folder.mkdir(parents=True, exist_ok=True)
                    writers[step] = pq.ParquetWriter(folder / "part-0.parquet.partial", rows.schema, compression="zstd")
                writers[step].write_table(rows)
                counts[step] = counts.get(step, 0) + rows.num_rows
            if i % 20 == 0 or i == len(starts):
                print(f"  steps {lo}-{hi}: {i}/{len(starts)} pages of {chunk_rows:,} rows", flush=True)
    for step, writer in writers.items():
        writer.close()
        folder = STORE / f"step={step:03d}"
        (folder / "part-0.parquet.partial").replace(folder / "part-0.parquet")
    if sum(counts.values()) != total:
        raise RuntimeError(f"downloaded {sum(counts.values()):,} rows, expected {total:,}")
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=["plan", "run"])
    parser.add_argument("--steps-per-chunk", type=int, default=10)
    parser.add_argument("--max-total-gb", type=float, default=300.0)
    parser.add_argument("--only", type=str, default="", help="run just these chunks, e.g. 1-10")
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()

    client = bq_common.get_client()
    active_job = reusable_job(client, ACTIVE_JOB)
    if active_job is None:
        raise SystemExit(f"{ACTIVE_JOB} result table has expired; rerun active_addresses (about 2 GB)")
    active_table = f"`{bq_common.result_table_id(active_job)}`"
    steps = steps_frame()
    months: dict = {}
    plan = {}
    for lo, hi in chunks(args.steps_per_chunk):
        sql, params = chunk_query(client, steps, lo, hi, active_table, months)
        plan[(lo, hi)] = (sql, params, bq_common.estimate_gb(client, sql, params))
        print(f"  steps {lo:3d}-{hi:3d}: {plan[(lo, hi)][2]:7.1f} GB", flush=True)
    total = sum(p[2] for p in plan.values())
    already = bq_common.billed_this_month_gb()
    print(f"  total {total:.1f} GB (these scripts billed {already:.1f} GB this month; free tier 1,000 GB)")
    if args.mode == "plan":
        return
    if total > args.max_total_gb:
        raise bq_common.CostLimitError(f"plan needs {total:.0f} GB, above --max-total-gb {args.max_total_gb:.0f}")

    manifest = load_manifest()
    wanted = set()
    if args.only:
        a, b = (int(x) for x in args.only.split("-"))
        wanted = {k for k in plan if k[0] >= a and k[1] <= b}
    for (lo, hi), (sql, params, estimate) in plan.items():
        key = f"{lo}-{hi}"
        if (wanted and (lo, hi) not in wanted) or manifest.get(key, {}).get("done"):
            continue
        name = f"window_transactions_{QUERY_VERSION}_{key}"
        job = reusable_job(client, name)
        if job is None:
            job = bq_common.run_query(client, sql, params, label=name, max_gb=estimate * 1.1 + 1)
            save_job(name, job)
        started = datetime.now(timezone.utc)
        counts = download_by_step(client, job, lo, hi, args.workers)
        manifest = load_manifest()
        manifest[key] = {"done": True, "job_id": job.job_id, "rows": sum(counts.values()),
                         "rows_by_step": {str(k): v for k, v in sorted(counts.items())},
                         "billed_gb": round((job.total_bytes_billed or 0) / 1e9, 3),
                         "download_seconds": (datetime.now(timezone.utc) - started).seconds}
        MANIFEST.write_text(json.dumps(manifest, indent=2))
        print(f"steps {lo}-{hi}: {sum(counts.values()):,} rows saved", flush=True)
    print(f"billed this month by these scripts: {bq_common.billed_this_month_gb():.1f} GB")


if __name__ == "__main__":
    main()
