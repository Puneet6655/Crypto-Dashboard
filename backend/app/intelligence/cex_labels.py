"""Exchange (CEX) address labels from Dune's community table `cex.addresses`.

The table is built from hand-curated seed lists in Dune's Spellbook (github.com/duneanalytics/spellbook,
models/_sector/cex/addresses), with the exchange name, a distinct name per address ("Binance 14"),
who added it and when. We download the rows for the chains the dashboard supports once a week with
one SQL query through Dune's API (DUNE_API_KEY in backend/.env) and look addresses up locally, so
graph mode spends no Dune credits per lookup.

Cost: one execution plus about 4 data points per row (the free plan has 2,500 credits a month and
counts 1,000 data points as 1 credit), so a weekly refresh of a few thousand rows costs a few dozen
credits a month.

Licence: Spellbook is under the Business Source License 1.1 until 3 March 2027 (then GPL-3.0). Its
grant forbids using it for a "Data or Analytics Platform" that gives third parties access to its
outputs. Use these labels for your own analysis only; do not commit the snapshot (it is under the
gitignored data/raw/) and check the licence before offering the dashboard to other people.
A label means "Dune's community lists this address as that exchange", not proof.

Command line (from backend/):  python -m app.intelligence.cex_labels refresh | status
"""
from __future__ import annotations

import json
import logging
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.config import BACKEND_DIR, get_settings
from app.validation import normalize_address

log = logging.getLogger("app.intelligence.cex_labels")

API = "https://api.dune.com/api/v1"
SOURCE_TITLE = "Dune cex.addresses (community labels)"
SOURCE_URL = "https://docs.dune.com/data-catalog/curated/cex-flows/addresses"
CHAINS = ("bitcoin", "ethereum", "base", "solana")
DEFAULT_DIR = BACKEND_DIR / "data" / "raw" / "labels"
FILE = "dune_cex_addresses.json"
MAX_AGE_DAYS = 7.0
# Non-EVM addresses are stored as their text cast to varbinary; EVM ones as 20 raw bytes.
SQL = """
SELECT blockchain,
       CASE WHEN blockchain IN ('bitcoin', 'solana') THEN from_utf8(address)
            ELSE '0x' || lower(to_hex(address)) END AS address,
       cex_name, distinct_name, added_by, CAST(added_date AS varchar) AS added_date
FROM cex.addresses
WHERE blockchain IN ('bitcoin', 'ethereum', 'base', 'solana')
"""


@dataclass(frozen=True)
class CexLabel:
    chain: str
    address: str
    exchange: str
    name: str | None
    added_by: str | None
    added_date: str | None

    def as_dict(self) -> dict:
        return {"exchange": self.exchange, "name": self.name, "added_by": self.added_by,
                "added_date": self.added_date, "source": SOURCE_TITLE}


def _key(chain: str, address: str) -> tuple[str, str]:
    address = (address or "").strip()
    if chain == "bitcoin":
        address = normalize_address(address)
    elif chain in ("ethereum", "base"):
        address = address.lower()
    return chain, address


class CexLabels:
    def __init__(self, rows: list[dict], retrieved_at: str | None = None):
        self.retrieved_at = retrieved_at
        self.labels: dict[tuple[str, str], CexLabel] = {}
        for r in rows:
            chain = r.get("blockchain")
            if chain not in CHAINS or not r.get("address") or not r.get("cex_name"):
                continue
            key = _key(chain, r["address"])
            self.labels[key] = CexLabel(chain, key[1], r["cex_name"], r.get("distinct_name"), r.get("added_by"),
                                        r.get("added_date"))

    def lookup(self, chain: str, address: str) -> CexLabel | None:
        return self.labels.get(_key(chain, address))

    def status(self) -> dict:
        counts: dict[str, int] = {}
        for chain, _ in self.labels:
            counts[chain] = counts.get(chain, 0) + 1
        return {"source": SOURCE_TITLE, "retrieved_at": self.retrieved_at, "addresses": len(self.labels),
                "per_chain": counts}


# ---- download -------------------------------------------------------------------------------------

def download(api_key: str, client: httpx.Client | None = None, poll_seconds: float = 2.0,
             timeout_seconds: float = 300.0) -> list[dict]:
    """Run the query on Dune and return every row. Raises RuntimeError with a readable message."""
    own = client is None
    client = client or httpx.Client(timeout=30)
    headers = {"X-Dune-Api-Key": api_key}
    try:
        started = client.post(f"{API}/sql/execute", headers=headers, json={"sql": SQL, "performance": "medium"})
        if started.status_code in (401, 403):
            raise RuntimeError("Dune refused the API key (check DUNE_API_KEY in backend/.env).")
        if started.status_code == 402:
            raise RuntimeError("Dune says the account is out of credits.")
        started.raise_for_status()
        execution = started.json()["execution_id"]
        deadline = time.monotonic() + timeout_seconds
        while True:
            state = client.get(f"{API}/execution/{execution}/status", headers=headers).json().get("state")
            if state == "QUERY_STATE_COMPLETED":
                break
            if state in ("QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED", "QUERY_STATE_EXPIRED"):
                raise RuntimeError(f"The Dune query ended as {state}.")
            if time.monotonic() > deadline:
                raise RuntimeError("The Dune query did not finish within 5 minutes.")
            time.sleep(poll_seconds)
        rows: list[dict] = []
        url, params = f"{API}/execution/{execution}/results", {"limit": 10_000}
        while url:
            page = client.get(url, headers=headers, params=params)
            page.raise_for_status()
            body = page.json()
            rows += (body.get("result") or {}).get("rows") or []
            url, params = body.get("next_uri"), None
        return rows
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not reach Dune: {exc.__class__.__name__}") from None
    finally:
        if own:
            client.close()


def refresh(folder: Path = DEFAULT_DIR, api_key: str | None = None, client: httpx.Client | None = None) -> CexLabels:
    """Download and save a new snapshot, then make it the one in use."""
    api_key = api_key or get_settings().dune_api_key
    if not api_key:
        raise RuntimeError("DUNE_API_KEY is not set in backend/.env.")
    rows = download(api_key, client)
    folder.mkdir(parents=True, exist_ok=True)
    retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tmp = folder / (FILE + ".tmp")
    tmp.write_text(json.dumps({"retrieved_at": retrieved_at, "source": SOURCE_URL, "rows": rows}), encoding="utf-8")
    tmp.replace(folder / FILE)
    labels = CexLabels(rows, retrieved_at)
    _set(labels, folder)
    log.info("Dune exchange labels: %s", labels.status())
    return labels


def load(folder: Path = DEFAULT_DIR) -> CexLabels:
    path = folder / FILE
    if not path.exists():
        return CexLabels([])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return CexLabels(data.get("rows") or [], data.get("retrieved_at"))
    except (OSError, ValueError) as exc:
        log.warning("Ignoring the saved Dune label snapshot: %s", exc)
        return CexLabels([])


def age_days(folder: Path = DEFAULT_DIR) -> float | None:
    path = folder / FILE
    return (time.time() - path.stat().st_mtime) / 86_400 if path.exists() else None


# ---- the copy in use ------------------------------------------------------------------------------

_lock = threading.Lock()
_current: tuple[Path, CexLabels] | None = None


def _set(labels: CexLabels, folder: Path) -> None:
    global _current
    with _lock:
        _current = (folder, labels)


def get_cex_labels(folder: Path = DEFAULT_DIR) -> CexLabels:
    """The labels in use, read from disk the first time."""
    global _current
    with _lock:
        if _current is None or _current[0] != folder:
            _current = (folder, load(folder))
        return _current[1]


async def keep_labels_fresh(api_key: str, folder: Path = DEFAULT_DIR, max_age_days: float = MAX_AGE_DAYS,
                            check_every_seconds: float = 6 * 3600) -> None:
    """Runs until cancelled: refreshes the snapshot when it is missing or older than max_age_days."""
    import asyncio

    while True:
        try:
            age = age_days(folder)
            if age is None or age > max_age_days:
                await asyncio.to_thread(refresh, folder, api_key)
        except Exception as exc:  # a Dune problem must never stop the API; keep the old snapshot
            log.warning("Dune exchange labels not refreshed: %s", exc)
        await asyncio.sleep(check_every_seconds)


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO)
    command = argv[0] if argv else "status"
    if command == "refresh":
        try:
            labels = refresh()
        except RuntimeError as exc:
            print(f"Failed: {exc}")
            return 1
        print(json.dumps(labels.status(), indent=2))
        return 0
    if command == "status":
        print(json.dumps({**get_cex_labels().status(), "age_days": age_days()}, indent=2))
        return 0
    print("Usage: python -m app.intelligence.cex_labels refresh | status")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
