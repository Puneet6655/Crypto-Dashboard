"""Safe BigQuery access for the training scripts.

Every query is dry-run first. If the estimate is above the limit the query is not sent.
Otherwise it runs with maximum_bytes_billed set, so BigQuery itself refuses to bill more.
Each query's estimate and billed bytes go to data/processed/bigquery/cost_log.csv.

BigQuery on-demand pricing bills bytes scanned; the first 1 TB per month is free.
The service-account key is read from BQ_KEY_PATH, else GOOGLE_APPLICATION_CREDENTIALS, else the
single *.json file in backend/data/. Its contents are never printed or logged. Keep it out of git.
"""
import csv
import math
import os
from datetime import datetime, timezone
from pathlib import Path

from google.cloud import bigquery
from google.oauth2 import service_account

BACKEND_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = BACKEND_DIR / "data" / "processed" / "bigquery"
COST_LOG = OUT_DIR / "cost_log.csv"
FREE_TIER_GB_PER_MONTH = 1000


class CostLimitError(RuntimeError):
    """Raised when a query's dry-run estimate is above the allowed limit."""


def find_key_path() -> Path:
    for name in ("BQ_KEY_PATH", "GOOGLE_APPLICATION_CREDENTIALS"):
        if os.getenv(name):
            return Path(os.environ[name])
    candidates = sorted((BACKEND_DIR / "data").glob("*.json"))
    if len(candidates) != 1:
        raise FileNotFoundError(
            "Set BQ_KEY_PATH to the service-account key file "
            f"(found {len(candidates)} .json files in backend/data/)."
        )
    return candidates[0]


def get_client() -> bigquery.Client:
    creds = service_account.Credentials.from_service_account_file(
        str(find_key_path()), scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    return bigquery.Client(credentials=creds, project=creds.project_id)


def estimate_gb(client: bigquery.Client, sql: str, params: list) -> float:
    """Bytes the query would scan, in GB (1e9 bytes). Free: nothing is run."""
    config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False, query_parameters=params)
    return client.query(sql, job_config=config).total_bytes_processed / 1e9


def _log(row: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    new = not COST_LOG.exists()
    with COST_LOG.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        if new:
            writer.writeheader()
        writer.writerow(row)


def run_query(client: bigquery.Client, sql: str, params: list, *, label: str, max_gb: float) -> bigquery.QueryJob:
    """Dry-run, refuse if above max_gb, then run with a hard billing cap. Returns the finished job."""
    estimate = estimate_gb(client, sql, params)
    print(f"[{label}] estimate {estimate:.2f} GB (limit {max_gb:.2f} GB)")
    if estimate > max_gb:
        raise CostLimitError(f"{label}: estimated {estimate:.1f} GB is above the {max_gb:.1f} GB limit")
    cap_bytes = max(int(math.ceil(max_gb * 1e9)), 100_000_000)
    config = bigquery.QueryJobConfig(
        query_parameters=params,
        maximum_bytes_billed=cap_bytes,
        labels={"source": "payments-dashboard", "step": label.replace("_", "-")[:63]},
    )
    started = datetime.now(timezone.utc)
    job = client.query(sql, job_config=config)
    job.result()
    billed = (job.total_bytes_billed or 0) / 1e9
    print(f"[{label}] done in {(datetime.now(timezone.utc) - started).seconds}s, billed {billed:.2f} GB"
          + (" (served from cache, free)" if job.cache_hit else ""))
    _log({
        "time_utc": started.isoformat(timespec="seconds"),
        "label": label,
        "estimated_gb": round(estimate, 3),
        "billed_gb": round(billed, 3),
        "cache_hit": bool(job.cache_hit),
        "job_id": job.job_id,
    })
    return job


def billed_this_month_gb() -> float:
    """GB billed this calendar month by these scripts, from the local log (not the whole project)."""
    if not COST_LOG.exists():
        return 0.0
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    with COST_LOG.open(encoding="utf-8") as handle:
        return sum(float(r["billed_gb"]) for r in csv.DictReader(handle) if r["time_utc"].startswith(month))


def result_table_id(job: bigquery.QueryJob) -> str:
    """Fully qualified name of a job's temporary result table (kept by BigQuery for about 24 hours)."""
    dest = job.destination
    return f"{dest.project}.{dest.dataset_id}.{dest.table_id}"


def download(client: bigquery.Client, job: bigquery.QueryJob, path: Path, workers: int = 8,
             chunk_rows: int = 250_000):
    """Save a finished job's result to Parquet through the free REST API. Returns the Arrow table.

    Large results are read in row ranges by several threads at once (REST paging is slow).
    """
    from concurrent.futures import ThreadPoolExecutor

    import pyarrow.parquet as pq

    destination = client.get_table(job.destination)
    total = destination.num_rows
    starts = list(range(0, total, chunk_rows)) or [0]

    def fetch(start: int) -> pa.Table:
        own_client = get_client()  # one client per thread
        return own_client.list_rows(destination, start_index=start, max_results=chunk_rows,
                                    page_size=50_000).to_arrow(create_bqstorage_client=False)

    # Chunks are written to the file as they arrive, so the whole result never sits in memory.
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".partial")
    rows, writer = 0, None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, part in enumerate(pool.map(fetch, starts), start=1):
            if writer is None:
                writer = pq.ParquetWriter(partial, part.schema)
            writer.write_table(part)
            rows += part.num_rows
            if len(starts) > 1:
                print(f"  downloaded chunk {i}/{len(starts)}", flush=True)
    writer.close()
    if rows != total:
        raise RuntimeError(f"downloaded {rows:,} rows, expected {total:,}")
    partial.replace(path)
    print(f"saved {rows:,} rows to {path}")
    return pq.read_table(path) if rows <= 1_000_000 else None
