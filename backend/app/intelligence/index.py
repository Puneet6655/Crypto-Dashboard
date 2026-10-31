"""The saved indicator lists, loaded into memory for exact address and website matching.

Website lookups check the host and each parent domain (a.b.example.com, b.example.com,
example.com) as plain set lookups, built once per snapshot load. Parent domains that are shared
hosting platforms (github.io, vercel.app, sites.google.com...) are skipped, and blacklists that
name such a platform itself (ScamSniffer and Phishing.Database list sites.google.com) are ignored
for it, so a listing of the platform never counts for every site published on it.

A popular (Tranco) parent domain with BUSY_PARENT or more blacklisted subdomains, such as the site
builder yolasite.com, is treated as a likely shared platform so its popularity does not count for
the sites on it; its own blacklist listing still counts.
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.intelligence import snapshots
from app.intelligence.sources import BLOCK, POPULAR, SHARED, SOURCES, IndicatorSource, Record, SiteList
from app.validation import normalize_address, split_url

log = logging.getLogger("app.intelligence")


def match_key(chain: str, address: str) -> str:
    """How addresses are compared. Bitcoin Base58 and Solana are case-sensitive and kept as written;
    bech32 (bc1...) and Ethereum-format (0x...) addresses are compared in lower case."""
    value = address.strip()
    if chain == "bitcoin":
        return normalize_address(value)
    if chain == "evm":
        return value.lower()
    if chain == "web":  # payment addresses reported for a website: 0x... or Bitcoin
        return value.lower() if value.lower().startswith("0x") else normalize_address(value)
    return value


# Payment addresses reported on website lists that we can compare: Bitcoin and Ethereum-format.
_SITE_ADDRESS_CODES = {"BTC", "ETH", "ETC", "BNB", "BSC", "USDT", "MATIC"}


# Platforms where anyone can publish a site or page, on top of the Polkadot{.js} allow list.
BUILTIN_SHARED_HOSTS = frozenset({
    "000webhostapp.com", "amazonaws.com", "azurewebsites.net", "bit.ly", "blogspot.com", "carrd.co",
    "cloudflare-ipfs.com", "cloudfront.net", "docs.google.com", "drive.google.com", "dweb.link", "firebaseapp.com",
    "firebasestorage.googleapis.com", "forms.gle", "framer.app", "framer.website", "github.com", "github.io",
    "githubusercontent.com", "gitbook.io", "gitlab.io", "glitch.me", "godaddysites.com", "herokuapp.com", "ipfs.io",
    "linktr.ee", "medium.com", "myshopify.com", "netlify.app", "ngrok-free.app", "ngrok.io", "notion.site",
    "onrender.com", "pages.dev", "r2.dev", "repl.co", "replit.app", "s3.amazonaws.com", "sites.google.com",
    "storage.googleapis.com", "surge.sh", "t.me", "telegra.ph", "tinyurl.com", "trycloudflare.com",
    "typedream.app", "vercel.app", "web.app", "webflow.io", "weebly.com", "weeblysite.com", "wixsite.com",
    "wordpress.com", "workers.dev",
})


# A parent domain with this many blacklisted subdomains is probably a free site builder or host.
BUSY_PARENT = 20
# Second-level labels of country domains (co.uk, com.au...), which are never a platform.
_SECOND_LEVEL = frozenset({"ac", "co", "com", "edu", "go", "gob", "gov", "mil", "ne", "net", "nic", "or", "org"})


def parent_domains(host: str) -> list[str]:
    """'a.b.example.com' -> ['b.example.com', 'example.com'] (never the bare top-level domain)."""
    labels = host.split(".")
    return [".".join(labels[i:]) for i in range(1, len(labels) - 1)]


@dataclass(frozen=True)
class LoadedList:
    source: IndicatorSource
    meta: dict
    records: dict[str, Record]  # match_key -> record (host name -> record for website lists)
    # Website lists only: single listed pages on shared hosts, and the reported payment addresses.
    pages: dict[str, list[Record]] = field(default_factory=dict)
    site_addresses: dict[str, Record] = field(default_factory=dict)
    # The big website lists (MetaMask, ScamSniffer, Polkadot, Phishing.Database, Tranco).
    site_list: SiteList | None = None

    @property
    def site_count(self) -> int:
        if self.site_list is not None:
            return len(self.site_list)
        return len(self.records) + sum(len(p) for p in self.pages.values())

    @property
    def retrieved_at(self) -> datetime | None:
        value = self.meta.get("retrieved_at")
        return datetime.fromisoformat(value) if value else None

    def snapshot_label(self) -> str:
        """e.g. 'snapshot of 2026-10-30 (532 addresses)'."""
        when = self.retrieved_at.strftime("%Y-%m-%d") if self.retrieved_at else "unknown date"
        if self.source.chain == "web":
            sites = self.site_count
            extra = f", {len(self.site_addresses):,} payment addresses" if self.site_addresses else ""
            return f"snapshot of {when} ({sites:,} websites{extra})"
        return f"snapshot of {when} ({len(self.records):,} addresses)"

    def list_changed_label(self) -> str | None:
        """e.g. 'list last changed 2022-06-28', from the source's own update date."""
        value = self.meta.get("list_updated_at")
        return f"list last changed {value[:10]}" if value else None


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


@dataclass(frozen=True)
class SiteHit:
    """A website found on a website list. `how` is "exact", "parent" (a parent domain is listed,
    e.g. login.scam.example when scam.example is) or "page" (one listed page on a shared host)."""

    source_id: str
    title: str
    label: str
    listed: str  # the listed host, plus the page path for "page" hits
    how: str
    record: Record
    rank: int | None = None  # Tranco rank


def _load_web(records: list[Record]) -> tuple[dict, dict, dict]:
    hosts, pages, addresses = {}, {}, {}
    for r in records:
        if r.path:
            pages.setdefault(r.address, []).append(r)
        else:
            hosts.setdefault(r.address, r)
        for code, address in r.addresses:
            if code in _SITE_ADDRESS_CODES:
                key = match_key("web", address)
                addresses.setdefault(key, Record(
                    address=address, family=f"{r.family or 'scam site'}, reported for {r.address}{r.path}",
                    description=r.description))
    return hosts, pages, addresses


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
            if isinstance(records, SiteList):
                self.lists[source.id] = LoadedList(source, meta, {}, site_list=records)
            elif source.chain == "web":
                self.lists[source.id] = LoadedList(source, meta, *_load_web(records))
            else:
                self.lists[source.id] = LoadedList(
                    source, meta, {match_key(source.chain, r.address): r for r in records})

        self.shared_hosts = BUILTIN_SHARED_HOSTS.union(*(
            loaded.site_list.hosts for loaded in self.lists.values()
            if loaded.source.site_role == SHARED and loaded.site_list is not None))
        self.busy_parents = self._busy_parents()

    def _busy_parents(self) -> dict[str, int]:
        """Popular parent domains with at least BUSY_PARENT blacklisted subdomains (counted per list).
        Unpopular ones are left out: those are usually one scammer's domain, not a platform."""
        popular = set().union(*(loaded.site_list.hosts for loaded in self.lists.values()
                                if loaded.source.site_role == POPULAR and loaded.site_list is not None))
        counts: Counter = Counter()
        for loaded in self.lists.values():
            if loaded.source.site_role != BLOCK:
                continue
            for host in (loaded.site_list.hosts if loaded.site_list is not None else loaded.records):
                parent = host.partition(".")[2]
                if "." in parent and parent.partition(".")[0] not in _SECOND_LEVEL:
                    counts[parent] += 1
        return {name: count for name, count in counts.items() if count >= BUSY_PARENT and name in popular}

    def shared_platform(self, host: str) -> str | None:
        """The shared hosting platform `host` is, or is published on (e.g. 'github.io'), else None."""
        names = [host, *parent_domains(host)]
        return (next((name for name in names if name in self.shared_hosts), None)
                or next((name for name in names[1:] if name in self.busy_parents), None))

    def loaded(self, source_id: str) -> LoadedList | None:
        return self.lists.get(source_id)

    def lookup(self, address: str, source_ids: list[str] | None = None) -> list[Hit]:
        """Exact matches for one address in the loaded lists (all of them by default)."""
        hits = []
        for source_id, loaded in self.lists.items():
            if source_ids is not None and source_id not in source_ids:
                continue
            table = loaded.site_addresses if loaded.source.chain == "web" else loaded.records
            record = table.get(match_key(loaded.source.chain, address))
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

    def lookup_site(self, value: str, source_ids: list[str] | None = None,
                    parents: bool = True) -> list[SiteHit]:
        """Matches for one URL or domain in the website lists (all of them by default).

        Checks the host itself, then (if `parents`) each parent domain that is not a shared hosting
        platform, then single listed pages on that host whose path the URL starts with. "www." is
        ignored on both sides.
        """
        split = split_url(value)
        if split is None:
            return []
        host, path = split
        candidates = [(host, "exact")]
        if parents:
            candidates += [(name, "parent") for name in parent_domains(host) if name not in self.shared_hosts]
        hits = []
        for source_id, loaded in self.lists.items():
            if loaded.source.chain != "web" or (source_ids is not None and source_id not in source_ids):
                continue
            mine = candidates
            if loaded.source.site_role == BLOCK:  # a blacklist naming a whole shared platform is ignored
                mine = [(name, how) for name, how in candidates if name not in self.shared_hosts]
            found = self._site_hit(loaded, host, path, mine)
            if found:
                hits.append(found)
        return hits

    @staticmethod
    def _site_hit(loaded: LoadedList, host: str, path: str, candidates: list[tuple[str, str]]) -> SiteHit | None:
        source = loaded.source

        def on_page(listed: str) -> bool:
            if "?" not in listed:  # a query string after a listed page still matches it
                page = path.split("?", 1)[0]
                return page == listed or page.startswith(listed.rstrip("/") + "/")
            return path == listed

        if loaded.site_list is not None:
            sites = loaded.site_list
            for name, how in candidates:
                if name in sites.hosts:
                    return SiteHit(source.id, source.title, source.label, name, how, Record(address=name),
                                   sites.ranks.get(name))
            for listed in sites.pages.get(host, ()):
                if on_page(listed):
                    return SiteHit(source.id, source.title, source.label, host + listed, "page",
                                   Record(address=host, path=listed))
            return None
        for name, how in candidates:
            if name in loaded.records:
                return SiteHit(source.id, source.title, source.label, name, how, loaded.records[name])
        for page in loaded.pages.get(host, []):
            if on_page(page.path):
                return SiteHit(source.id, source.title, source.label, page.address + page.path, "page", page)
        return None

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
                "record_count": (loaded.site_count if source.chain == "web" else len(loaded.records)) if loaded else None,
                "retrieved_at": meta.get("retrieved_at"),
                "list_updated_at": meta.get("list_updated_at"),
                "sha256": meta.get("sha256"),
                "age_hours": round(age, 1) if age is not None else None,
                "stale": age is None or (max_age_hours is not None and age >= max_age_hours),
                "licence": source.licence,
                "note": source.note,
                "site_role": source.site_role,
                "use_for_training": source.use_for_training,
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
