"""The saved indicator lists, loaded into memory for exact address matching."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.intelligence import snapshots
from app.intelligence.sources import SOURCES, IndicatorSource, Record
from app.validation import normalize_address

log = logging.getLogger("app.intelligence")


def match_key(chain: str, address: str) -> str:
    """How addresses are compared. Bitcoin Base58 and Solana are case-sensitive and kept as written;
    bech32 (bc1...) and Ethereum-format (0x...) addresses are compared in lower case."""
    value = address.strip()
    if chain == "bitcoin":
        return normalize_address(value)
    if chain == "evm":
        return value.lower()
    return value


@dataclass(frozen=True)
class LoadedList:
    source: IndicatorSource
    meta: dict
    records: dict[str, Record]  # match_key -> record

    @property
    def retrieved_at(self) -> datetime | None:
        value = self.meta.get("retrieved_at")
        return datetime.fromisoformat(value) if value else None

    def snapshot_label(self) -> str:
        """e.g. 'snapshot of 2026-10-30 (532 addresses)'."""
        when = self.retrieved_at.strftime("%Y-%m-%d") if self.retrieved_at else "unknown date"
        return f"snapshot of {when} ({len(self.records):,} addresses)"


@dataclass(frozen=True)
class Hit:
    source_id: str
    title: str
    label: str
    address: str
    family: str | None
    first_seen: datetime | None
    last_seen: datetime | None
    listed_at: datetime | None
    snapshot_retrieved_at: str | None
    list_updated_at: str | None


class IndicatorIndex:
    def __init__(self, folder: Path):
        self.folder = folder
        self.lists: dict[str, LoadedList] = {}
        for source in SOURCES.values():
            meta = snapshots.read_meta(folder, source.id)
            if meta is None:
                continue
            try:
                records = snapshots.load_records(folder, source)
            except (OSError, ValueError) as exc:
                log.warning("Ignoring the saved %s snapshot: %s", source.id, exc)
                continue
            self.lists[source.id] = LoadedList(
                source, meta, {match_key(source.chain, r.address): r for r in records})

    def loaded(self, source_id: str) -> LoadedList | None:
        return self.lists.get(source_id)

    def lookup(self, address: str, source_ids: list[str] | None = None) -> list[Hit]:
        """Exact matches for one address in the loaded lists (all of them by default)."""
        hits = []
        for source_id, loaded in self.lists.items():
            if source_ids is not None and source_id not in source_ids:
                continue
            record = loaded.records.get(match_key(loaded.source.chain, address))
            if record is None:
                continue
            hits.append(Hit(
                source_id=source_id, title=loaded.source.title, label=loaded.source.label,
                address=record.address, family=record.family, first_seen=record.first_seen,
                last_seen=record.last_seen, listed_at=record.listed_at,
                snapshot_retrieved_at=loaded.meta.get("retrieved_at"),
                list_updated_at=loaded.meta.get("list_updated_at"),
            ))
        return hits

    def status(self, max_age_hours: float | None = None) -> list[dict]:
        """One entry per known source: loaded or not, counts and dates."""
        out = []
        for source in SOURCES.values():
            loaded = self.lists.get(source.id)
            meta = loaded.meta if loaded else snapshots.read_meta(self.folder, source.id) or {}
            age = snapshots.age_hours(meta) if meta else None
            out.append({
                "source_id": source.id,
                "title": source.title,
                "chain": source.chain,
                "url": source.url,
                "loaded": loaded is not None,
                "record_count": len(loaded.records) if loaded else None,
                "retrieved_at": meta.get("retrieved_at"),
                "list_updated_at": meta.get("list_updated_at"),
                "sha256": meta.get("sha256"),
                "age_hours": round(age, 1) if age is not None else None,
                "stale": age is None or (max_age_hours is not None and age >= max_age_hours),
                "licence": source.licence,
                "note": source.note,
            })
        return out


_index: IndicatorIndex | None = None


def get_indicator_index() -> IndicatorIndex:
    """The process-wide index, loaded from disk on first use. Also a FastAPI dependency."""
    global _index
    if _index is None:
        from app.config import get_settings

        _index = IndicatorIndex(get_settings().indicators_dir)
    return _index


def reload_indicator_index(folder: Path | None = None) -> IndicatorIndex:
    """Load the saved snapshots again (after a refresh)."""
    global _index
    if folder is None:
        from app.config import get_settings

        folder = get_settings().indicators_dir
    _index = IndicatorIndex(folder)
    return _index
