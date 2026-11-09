"""Incremental store of every recent Bitcoin transaction from BigQuery, for recent batch scoring.

Run from backend/ with the root .venv:
    python -m training.recent_store plan     # dry runs only: blocks missing and GB per chunk
    python -m training.recent_store pull     # pulls only blocks not saved yet (refuses above --max-total-gb)

What is kept: every transaction of the newest SPAN_BLOCKS blocks (5,040 = the 4,032-block feature
window + one week). Scoring looks at addresses active in the newest week (1,008 blocks); an address
whose newest transaction is a week old still needs the full 4,032 blocks before it, hence the extra week.

Files (gitignored) in data/raw/bigquery/transactions/recent/:
  recent_20261027_20261102_part-{0..3}.parquet  the first pull (2 Nov 2026), without virtual size
  vsize_<lo>_<hi>.parquet      tx_hash + vsize for those files (a cheap one-column backfill)
  blocks_<lo>_<hi>.parquet     later pulls, with vsize (sql/recent_transactions.sql)
  manifest.json                block range of every file
A rerun pulls only blocks newer than the newest saved one (and any gap at the old end).
Columns: tx_hash, block_height, block_time, is_coinbase, fee_sats, vsize, inputs/outputs
(list of {address, value}; address NULL unless exactly one standard address).
"""
import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from google.cloud import bigquery

from app.features.wallet_window import WEEK_BLOCKS, WINDOW_BLOCKS
from training import bq_common
from training.pull_bigquery import SQL_DIR, STD_TYPES, month_bounds, reusable_job, save_job
from training.transaction_store import STORE as STEP_STORE

STORE = STEP_STORE / "recent"
MANIFEST = STORE / "manifest.json"
SPAN_BLOCKS = WINDOW_BLOCKS + WEEK_BLOCKS
CHUNK_BLOCKS = 500  # about 2M transactions (~1 GB result) per query
LEGACY_PREFIX = "recent_20261027_20261102_part-"
QUERY_VERSION = "v1"


def load_manifest() -> dict:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text())
    files = []
    for path in sorted(STORE.glob(f"{LEGACY_PREFIX}*.parquet")):  # register the first pull
        heights = pq.read_table(path, columns=["block_height"]).column("block_height")
        mm = pc.min_max(heights).as_py()
        files.append({"file": path.name, "block_lo": mm["min"], "block_hi": mm["max"], "rows": len(heights),
                      "has_vsize": False, "billed_gb": None, "note": "first recent pull, 2 Nov 2026"})
    return {"files": files, "vsize_files": [], "pulls": []}


def save_manifest(manifest: dict) -> None:
    STORE.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=2))


def covered_ranges(manifest: dict) -> list[tuple[int, int]]:
    spans = sorted((f["block_lo"], f["block_hi"]) for f in manifest["files"])
    merged: list[list[int]] = []
    for lo, hi in spans:
        if merged and lo <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return [tuple(m) for m in merged]


def missing_ranges(need_lo: int, need_hi: int, covered: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out, cursor = [], need_lo
    for lo, hi in covered:
        if hi < cursor:
            continue
        if lo > need_hi:
            break
        if lo > cursor:
            out.append((cursor, min(lo - 1, need_hi)))
        cursor = max(cursor, hi + 1)
    if cursor <= need_hi:
        out.append((cursor, need_hi))
    return out


def split(lo: int, hi: int, size: int = CHUNK_BLOCKS) -> list[tuple[int, int]]:
    return [(a, min(a + size - 1, hi)) for a in range(lo, hi + 1, size)]


def bigquery_tip(client) -> tuple[int, str]:
    """Newest block in BigQuery's copy (it lags the chain by roughly 20 minutes). Tiny query."""
    sql = """SELECT MAX(number) AS tip, MAX(timestamp) AS ts FROM `bigquery-public-data.crypto_bitcoin.blocks`
             WHERE timestamp_month >= DATE_SUB(DATE_TRUNC(CURRENT_DATE(), MONTH), INTERVAL 1 MONTH)"""
    row = list(bq_common.run_query(client, sql, [], label="recent_tip", max_gb=0.2).result())[0]
    return int(row["tip"]), row["ts"].isoformat()


def range_query(client, lo: int, hi: int) -> tuple[str, list]:
    month_lo, month_hi = month_bounds(client, lo, hi)
    params = [
        bigquery.ScalarQueryParameter("month_lo", "DATE", month_lo),
        bigquery.ScalarQueryParameter("month_hi", "DATE", month_hi),
        bigquery.ScalarQueryParameter("block_lo", "INT64", lo),
        bigquery.ScalarQueryParameter("block_hi", "INT64", hi),
        bigquery.ArrayQueryParameter("std_types", "STRING", STD_TYPES),
    ]
    return (SQL_DIR / "recent_transactions.sql").read_text(), params


def vsize_query(client, lo: int, hi: int) -> tuple[str, list]:
    month_lo, month_hi = month_bounds(client, lo, hi)
    sql = """SELECT `hash` AS tx_hash, CAST(virtual_size AS INT64) AS vsize
             FROM `bigquery-public-data.crypto_bitcoin.transactions`
             WHERE block_timestamp_month BETWEEN @month_lo AND @month_hi AND block_number BETWEEN @block_lo AND @block_hi"""
    params = [bigquery.ScalarQueryParameter("month_lo", "DATE", month_lo),
              bigquery.ScalarQueryParameter("month_hi", "DATE", month_hi),
              bigquery.ScalarQueryParameter("block_lo", "INT64", lo),
              bigquery.ScalarQueryParameter("block_hi", "INT64", hi)]
    return sql, params


def download(job, path: Path, workers: int = 8, chunk_rows: int = 200_000) -> int:
    """A finished job's result to Parquet through the free REST API, with retries on network errors."""
    client = bq_common.get_client()
    table = client.get_table(job.destination)
    total = table.num_rows
    starts = list(range(0, total, chunk_rows)) or [0]

    def fetch(start: int) -> pa.Table:
        for attempt in range(5):
            try:
                return bq_common.get_client().list_rows(table, start_index=start, max_results=chunk_rows,
                                                        page_size=25_000).to_arrow(create_bqstorage_client=False)
            except Exception as exc:  # noqa: BLE001
                if attempt == 4:
                    raise
                print(f"  rows from {start:,} failed ({type(exc).__name__}); retrying", flush=True)
                time.sleep(10 * 2 ** attempt)

    partial = path.with_suffix(".partial")
    rows, writer = 0, None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, part in enumerate(pool.map(fetch, starts), start=1):
            if writer is None:
                writer = pq.ParquetWriter(partial, part.schema, compression="zstd")
            writer.write_table(part, row_group_size=250_000)
            rows += part.num_rows
            if i % 10 == 0 or i == len(starts):
                print(f"  {path.name}: {i}/{len(starts)} pages", flush=True)
    writer.close()
    if rows != total:
        raise RuntimeError(f"downloaded {rows:,} rows, expected {total:,}")
    partial.replace(path)
    return rows


def plan(client, span: int = SPAN_BLOCKS) -> dict:
    manifest = load_manifest()
    tip, tip_time = bigquery_tip(client)
    need_lo = tip - span + 1
    chunks = [c for lo, hi in missing_ranges(need_lo, tip, covered_ranges(manifest)) for c in split(lo, hi)]
    jobs = []
    for lo, hi in chunks:
        sql, params = range_query(client, lo, hi)
        jobs.append(("blocks", lo, hi, sql, params, bq_common.estimate_gb(client, sql, params)))
    legacy = [f for f in manifest["files"] if not f["has_vsize"]]
    have_vsize = {(v["block_lo"], v["block_hi"]) for v in manifest["vsize_files"]}
    if legacy:
        lo, hi = min(f["block_lo"] for f in legacy), max(f["block_hi"] for f in legacy)
        if (lo, hi) not in have_vsize:
            sql, params = vsize_query(client, lo, hi)
            jobs.append(("vsize", lo, hi, sql, params, bq_common.estimate_gb(client, sql, params)))
    for kind, lo, hi, *_, gb in jobs:
        print(f"  {kind} {lo:,}-{hi:,}: {gb:.2f} GB (estimate)", flush=True)
    total = sum(j[-1] for j in jobs)
    print(f"tip {tip:,} ({tip_time}); keep blocks {need_lo:,}-{tip:,}; {len(jobs)} queries, {total:.2f} GB estimated")
    return {"tip": tip, "tip_time": tip_time, "need_lo": need_lo, "jobs": jobs, "estimate_gb": total}


def pull(client, max_total_gb: float, workers: int) -> dict:
    p = plan(client)
    if p["estimate_gb"] > max_total_gb:
        raise bq_common.CostLimitError(f"pull needs {p['estimate_gb']:.1f} GB, above --max-total-gb {max_total_gb}")
    billed = 0.0
    for kind, lo, hi, sql, params, estimate in p["jobs"]:
        name = f"recent_{kind}_{QUERY_VERSION}_{lo}_{hi}"
        job = reusable_job(client, name)
        if job is None:
            job = bq_common.run_query(client, sql, params, label=name, max_gb=estimate * 1.1 + 0.2)
            save_job(name, job)
            billed += (job.total_bytes_billed or 0) / 1e9
        path = STORE / f"{kind}_{lo}_{hi}.parquet"
        started = time.time()
        rows = download(job, path, workers)
        manifest = load_manifest()
        record = {"file": path.name, "block_lo": lo, "block_hi": hi, "rows": rows,
                  "billed_gb": round((job.total_bytes_billed or 0) / 1e9, 3), "job_id": job.job_id,
                  "download_seconds": round(time.time() - started)}
        if kind == "blocks":
            manifest["files"].append({**record, "has_vsize": True})
        else:
            manifest["vsize_files"].append(record)
        save_manifest(manifest)
        print(f"{path.name}: {rows:,} rows in {record['download_seconds']} s", flush=True)
    manifest = load_manifest()
    manifest["pulls"].append({"tip": p["tip"], "tip_time": p["tip_time"], "need_lo": p["need_lo"],
                              "queries": len(p["jobs"]), "billed_gb": round(billed, 3),
                              "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    manifest["tip"], manifest["tip_time"] = p["tip"], p["tip_time"]
    save_manifest(manifest)
    print(f"pulled {len(p['jobs'])} ranges, billed {billed:.2f} GB; store covers {covered_ranges(manifest)}")
    return manifest


def read_blocks(lo: int, hi: int, columns: list[str] | None = None):
    """Yield (file name, Arrow table) for every saved file overlapping blocks lo-hi, rows inside lo-hi only.

    vsize is joined in for the first-pull files from their backfill file."""
    import pyarrow.dataset as ds

    manifest = load_manifest()
    vsize = None
    for f in sorted(manifest["files"], key=lambda f: f["block_lo"]):
        if f["block_hi"] < lo or f["block_lo"] > hi:
            continue
        cols = columns
        want_vsize = columns is None or "vsize" in columns
        if not f["has_vsize"] and cols is not None:
            cols = [c for c in cols if c != "vsize"] + (["tx_hash"] if want_vsize and "tx_hash" not in cols else [])
        table = ds.dataset(STORE / f["file"]).to_table(
            columns=cols, filter=(ds.field("block_height") >= lo) & (ds.field("block_height") <= hi))
        if not f["has_vsize"] and want_vsize:
            if vsize is None:
                vsize = pa.concat_tables([pq.read_table(STORE / v["file"]) for v in manifest["vsize_files"]])
            # (Arrow joins do not carry list columns, so look the hashes up instead.)
            pos = pc.index_in(table.column("tx_hash"), value_set=vsize.column("tx_hash"))
            table = table.append_column("vsize", pc.take(vsize.column("vsize"), pos))
            if columns is not None:
                table = table.select(columns)
        yield f["file"], table


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=["plan", "pull"])
    parser.add_argument("--max-total-gb", type=float, default=25.0)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    client = bq_common.get_client()
    if args.mode == "plan":
        plan(client)
    else:
        pull(client, args.max_total_gb, args.workers)


if __name__ == "__main__":
    main()
