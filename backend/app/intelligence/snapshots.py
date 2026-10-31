"""Download, check and save indicator snapshots, each with a small provenance file beside it.

For every source in sources.SOURCES the folder holds:

* <id>.json       the file exactly as downloaded (.yaml for CryptoScamDB, .txt for Phishing.Database,
                  .zip for Tranco). Two lists cut from one file (MetaMask, Polkadot) each keep a copy.
* <id>.meta.json  where and when it came from, its SHA-256, record count and the list's own
                  update date (newest commit for the OFAC lists, newest submission for Ransomwhere)

A new download replaces the old one only if it parses and has at least half as many records as
before; otherwise the old snapshot stays and the refresh reports why. After each refresh,
bitcoin_labels.csv lists every Bitcoin address with its source, family and dates (for training),
leaving out lists whose licence does not allow that (CryptoScamDB).
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.intelligence.sources import SOURCES, IndicatorSource, Record, SiteList, SnapshotRejected

log = logging.getLogger("app.intelligence")

PARSER_VERSION = 1
MIN_SHARE_OF_PREVIOUS = 0.5  # refuse a new list with fewer than half the old records
LABELS_FILE = "bitcoin_labels.csv"
LABEL_COLUMNS = ("address", "source", "label", "family", "first_seen", "last_seen", "payment_count",
                 "listed_at", "list_updated_at", "snapshot_retrieved_at")
USER_AGENT = "payments-dashboard/0.4 (student research project)"


@dataclass(frozen=True)
class RefreshResult:
    source_id: str
    status: str  # updated, unchanged, rejected or failed
    detail: str
    record_count: int | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def raw_path(folder: Path, source_id: str, extension: str = "json") -> Path:
    return folder / f"{source_id}.{extension}"


def meta_path(folder: Path, source_id: str) -> Path:
    return folder / f"{source_id}.meta.json"


def read_meta(folder: Path, source_id: str) -> dict | None:
    try:
        return json.loads(meta_path(folder, source_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_records(folder: Path, source: IndicatorSource) -> list[Record]:
    """Parse a saved snapshot again (raises OSError or ValueError if it is missing or broken)."""
    return source.parse(source.decode(raw_path(folder, source.id, source.extension).read_bytes()))


def age_hours(meta: dict | None, now: datetime | None = None) -> float | None:
    if not meta or not meta.get("retrieved_at"):
        return None
    retrieved = datetime.fromisoformat(meta["retrieved_at"])
    return ((now or _utcnow()) - retrieved).total_seconds() / 3600


def stale_sources(folder: Path, max_age_hours: float) -> list[str]:
    """Sources with no snapshot yet, or one older than max_age_hours."""
    stale = []
    for source_id in SOURCES:
        age = age_hours(read_meta(folder, source_id))
        if age is None or age >= max_age_hours:
            stale.append(source_id)
    return stale


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _list_updated_at(client: httpx.Client, source: IndicatorSource, records: list[Record]) -> str | None:
    """When the list itself last changed: its newest commit (OFAC) or newest submission (Ransomwhere)."""
    if source.commits_url:
        try:
            response = client.get(source.commits_url, headers={"Accept": "application/vnd.github+json"})
            response.raise_for_status()
            return response.json()[0]["commit"]["committer"]["date"]
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            return None  # GitHub's API is rate limited; the snapshot is still usable without the date
    if isinstance(records, SiteList):
        return None
    listed = [r.listed_at for r in records if r.listed_at]
    return _iso(max(listed)) if listed else None


def refresh(folder: Path, source_ids: list[str] | None = None, client: httpx.Client | None = None,
            force: bool = False) -> list[RefreshResult]:
    """Download the given sources (default: all). A failure keeps that source's previous snapshot."""
    folder.mkdir(parents=True, exist_ok=True)
    own_client = client is None
    client = client or httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    results = []
    bodies: dict[str, bytes] = {}  # url -> body, so a file shared by two lists is downloaded once
    try:
        for source_id in source_ids or list(SOURCES):
            results.append(_refresh_one(folder, SOURCES[source_id], client, force, bodies))
    finally:
        if own_client:
            client.close()
    if any(r.status in ("updated", "unchanged") for r in results):
        write_labels_csv(folder)
    return results


def _refresh_one(folder: Path, source: IndicatorSource, client: httpx.Client, force: bool,
                 bodies: dict[str, bytes] | None = None) -> RefreshResult:
    bodies = {} if bodies is None else bodies
    try:
        if source.url not in bodies:
            response = client.get(source.url)
            response.raise_for_status()
            bodies[source.url] = response.content
        body = bodies[source.url]
        records = source.parse(source.decode(body))
    except SnapshotRejected as exc:
        log.warning("Kept the old %s snapshot: %s", source.id, exc)
        return RefreshResult(source.id, "rejected", f"Unexpected format: {exc}")
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("Could not download %s: %s", source.id, exc)
        return RefreshResult(source.id, "failed", f"Download failed: {exc.__class__.__name__}: {exc}")

    old = read_meta(folder, source.id)
    old_count = (old or {}).get("record_count") or 0
    if not force and old_count and len(records) < MIN_SHARE_OF_PREVIOUS * old_count:
        detail = f"New list has {len(records)} entries, the saved one {old_count}; kept the saved one (use --force)"
        log.warning("%s: %s", source.id, detail)
        return RefreshResult(source.id, "rejected", detail, len(records))

    sha256 = hashlib.sha256(body).hexdigest()
    unchanged = bool(old) and old.get("sha256") == sha256
    meta = {
        "source_id": source.id,
        "title": source.title,
        "url": source.url,
        "chain": source.chain,
        "label": source.label,
        "licence": source.licence,
        "note": source.note,
        "retrieved_at": _iso(_utcnow()),
        "list_updated_at": _list_updated_at(client, source, records),
        "sha256": sha256,
        "bytes": len(body),
        "record_count": len(records),
        "parser_version": PARSER_VERSION,
        "first_retrieved_at": (old or {}).get("first_retrieved_at") if unchanged else None,
        "previous_record_count": old_count or None,
    }
    meta["first_retrieved_at"] = meta["first_retrieved_at"] or meta["retrieved_at"]
    if not unchanged:
        _write_atomic(raw_path(folder, source.id, source.extension), body)
    _write_atomic(meta_path(folder, source.id), json.dumps(meta, indent=2).encode("utf-8"))
    status = "unchanged" if unchanged else "updated"
    change = "" if not old_count or unchanged else f" (was {old_count})"
    unit = "websites" if source.chain == "web" else "addresses"
    return RefreshResult(source.id, status, f"{len(records)} {unit}{change}", len(records))


def write_labels_csv(folder: Path) -> Path | None:
    """One row per (Bitcoin address, source) from the saved snapshots, for the training pipeline."""
    rows = []
    for source in SOURCES.values():
        if source.chain != "bitcoin" or not source.use_for_training:
            continue
        meta = read_meta(folder, source.id)
        try:
            records = load_records(folder, source)
        except (OSError, ValueError):
            continue
        for r in records:
            rows.append({
                "address": r.address,
                "source": source.id,
                "label": "sanctioned" if source.id.startswith("ofac") else "ransomware",
                "family": r.family or "",
                "first_seen": _iso(r.first_seen) or "",
                "last_seen": _iso(r.last_seen) or "",
                "payment_count": "" if r.payment_count is None else r.payment_count,
                "listed_at": _iso(r.listed_at) or "",
                "list_updated_at": (meta or {}).get("list_updated_at") or "",
                "snapshot_retrieved_at": (meta or {}).get("retrieved_at") or "",
            })
    if not rows:
        return None
    path = folder / LABELS_FILE
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LABEL_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)
    return path
