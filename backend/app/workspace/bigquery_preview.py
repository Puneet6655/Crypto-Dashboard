"""BigQuery refresh (under development): estimate what loading recent Bitcoin transactions would cost.

Only dry runs are sent, which BigQuery does not bill. Running the query and storing its result is
not built yet, because where the data will be hosted has not been decided.

The service-account key is found the same way as in training/bq_common.py: BQ_KEY_PATH, else
GOOGLE_APPLICATION_CREDENTIALS, else the single *.json file in backend/data/. It is never returned.
"""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

from app.config import BACKEND_DIR

TABLE = "bigquery-public-data.crypto_bitcoin.transactions"
FREE_TIER_GB_PER_MONTH = 1000
MAX_DAYS = 92
BASE_COLUMNS = ("`hash`", "block_hash", "block_number", "block_timestamp", "input_count", "output_count",
                "input_value", "output_value", "fee", "is_coinbase")


class NotConfigured(RuntimeError):
    pass


def library_installed() -> bool:
    try:
        import google.cloud.bigquery  # noqa: F401
    except ImportError:
        return False
    return True


def find_key_path() -> Path | None:
    for name in ("BQ_KEY_PATH", "GOOGLE_APPLICATION_CREDENTIALS"):
        if os.getenv(name):
            path = Path(os.environ[name])
            return path if path.is_file() else None
    candidates = sorted((BACKEND_DIR / "data").glob("*.json"))
    return candidates[0] if len(candidates) == 1 else None


def project_id() -> str | None:
    path = find_key_path()
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("project_id")
    except (OSError, ValueError):
        return None


def build_sql(include_inputs_outputs: bool) -> str:
    columns = list(BASE_COLUMNS) + (["inputs", "outputs"] if include_inputs_outputs else [])
    return (
        f"SELECT {', '.join(columns)}\n"
        f"FROM `{TABLE}`\n"
        "WHERE block_timestamp_month BETWEEN DATE_TRUNC(@start_date, MONTH) AND DATE_TRUNC(@end_date, MONTH)\n"
        "  AND DATE(block_timestamp) BETWEEN @start_date AND @end_date"
    )


def dates_problem(start: date, end: date) -> str | None:
    if start > end:
        return "The start date must be on or before the end date."
    if start < date(2009, 1, 3):
        return "Bitcoin starts on 3 January 2009."
    if end > date.today():
        return "The end date cannot be in the future."
    if (end - start).days + 1 > MAX_DAYS:
        return f"Choose at most {MAX_DAYS} days at a time."
    return None


def estimate(start: date, end: date, include_inputs_outputs: bool) -> dict:
    """Dry-run the query and return how much it would scan. Raises NotConfigured if BigQuery is not set up."""
    if not library_installed():
        raise NotConfigured("The google-cloud-bigquery package is not installed in this Python environment.")
    key = find_key_path()
    if key is None:
        raise NotConfigured("No service-account key found. Set BQ_KEY_PATH in backend/.env.")

    from google.cloud import bigquery
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(
        str(key), scopes=["https://www.googleapis.com/auth/cloud-platform"])
    client = bigquery.Client(credentials=creds, project=creds.project_id)
    sql = build_sql(include_inputs_outputs)
    config = bigquery.QueryJobConfig(
        dry_run=True,
        use_query_cache=False,
        query_parameters=[bigquery.ScalarQueryParameter("start_date", "DATE", start),
                          bigquery.ScalarQueryParameter("end_date", "DATE", end)],
    )
    job = client.query(sql, job_config=config)
    gb = job.total_bytes_processed / 1e9
    return {
        "sql": sql,
        "start_date": start,
        "end_date": end,
        "days": (end - start).days + 1,
        "include_inputs_outputs": include_inputs_outputs,
        "bytes_processed": job.total_bytes_processed,
        "gb_processed": round(gb, 2),
        "share_of_free_tier": round(gb / FREE_TIER_GB_PER_MONTH, 4),
        "billed": False,
    }
