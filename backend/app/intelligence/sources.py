"""The indicator lists we download, and the checks each file must pass before it replaces the old one.

None needs an account or key (see the data source guide, sections 2 and 4):

* OFAC sanctioned addresses for Bitcoin (XBT), Ethereum-format (ETH) and Solana (SOL), as
  extracted nightly from OFAC's SDN list by github.com/0xB10C/ofac-sanctioned-digital-currency-addresses
  (MIT). A community copy of the official list: verify important hits against OFAC itself.
* Ransomwhere (ransomwhe.re): crowdsourced Bitcoin ransomware payment addresses with family
  and payment times. Addresses appear 90 days after submission; check the Zenodo record before
  sharing the data further.
* CryptoScamDB (github.com/CryptoScamDB): a blacklist of scam and phishing websites, with the
  payment addresses some of them used, and a whitelist of genuine crypto websites. The repositories
  carry NO licence, so the data is only downloaded into the git-ignored data folder for lookups:
  never commit it, copy it into reports or use it as training labels. The lists stopped being
  updated in 2022 (blacklist) and 2021 (whitelist), so they miss newer scams.
* Newer scam-website lists, all kept out of training labels: MetaMask's eth-phishing-detect
  (DBAD 1.2), ScamSniffer (GPL-3.0), Polkadot{.js} phishing (Apache-2.0) and the general
  Phishing.Database (MIT, not crypto-specific). Tranco's top 100,000 sites says a site is popular:
  context only, never proof it is safe. Its inputs include CC BY-NC data, so internal,
  non-commercial use only.

The big website lists are held as sets of host names (SiteList), not one Record per site.
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from app.validation import split_url

OFAC_REPO = "0xB10C/ofac-sanctioned-digital-currency-addresses"
OFAC_RAW = f"https://raw.githubusercontent.com/{OFAC_REPO}/lists/"


class SnapshotRejected(ValueError):
    """The downloaded file does not look like the list we expect, so the old snapshot is kept."""


@dataclass(frozen=True)
class Record:
    """One listed address or website, as stored in the index and the labels CSV.

    For a website list, `address` is the host name (see validation.split_url), `path` is set when
    only one page on a shared host is listed, and `addresses` holds the payment addresses the
    source reported for the site as (currency code, address) pairs, e.g. ("ETH", "0x...").
    """

    address: str
    family: str | None = None  # ransomware family, or the scam category for websites
    first_seen: datetime | None = None  # earliest payment the source lists
    last_seen: datetime | None = None
    payment_count: int | None = None
    listed_at: datetime | None = None  # when the source added the address
    path: str = ""
    addresses: tuple[tuple[str, str], ...] = ()
    description: str | None = None


@dataclass(frozen=True)
class SiteList:
    """A plain website list: host names (lower case, punycode, no "www."), plus single listed pages
    on shared hosts (host -> paths, e.g. sites.google.com -> /view/fake-wallet). `ranks` holds
    Tranco ranks; `lookalike_targets` the names MetaMask checks for look-alikes."""

    hosts: frozenset[str]
    pages: dict[str, tuple[str, ...]] = field(default_factory=dict)
    ranks: dict[str, int] = field(default_factory=dict)
    lookalike_targets: tuple[str, ...] = ()
    skipped: int = 0  # entries that were not usable host names

    def __len__(self) -> int:
        return len(self.hosts) + sum(len(p) for p in self.pages.values())


# What a website list says about a site on it: scam/phishing, genuine or reviewed, a shared
# hosting platform, or popular (Tranco).
BLOCK, ALLOW, SHARED, POPULAR = "block", "allow", "shared_host", "popular"


@dataclass(frozen=True)
class IndicatorSource:
    id: str
    title: str
    url: str
    chain: str  # "bitcoin", "evm", "solana", or "web" for website lists: decides how entries are compared
    label: str  # what a match means, in plain words
    licence: str
    parse: Callable[[object], list[Record]]
    # GitHub API URL whose newest commit date says when the list file last changed.
    commits_url: str | None = None
    note: str | None = None
    file_format: str = "json"  # "json", "yaml", "text" or "zip" (the first file inside, as bytes)
    # May the addresses go into bitcoin_labels.csv for training? Not for unlicensed lists.
    use_for_training: bool = True
    # Website lists only: BLOCK, ALLOW, SHARED or POPULAR (see above). crypto_specific=False marks
    # a general phishing list, reported with less weight.
    site_role: str | None = None
    crypto_specific: bool = True

    @property
    def extension(self) -> str:
        return {"yaml": "yaml", "text": "txt", "zip": "zip"}.get(self.file_format, "json")

    def decode(self, body: bytes) -> object:
        """The downloaded bytes as Python data (raises ValueError if they do not decode)."""
        if self.file_format == "text":
            return body.decode("utf-8-sig")
        if self.file_format == "zip":
            return _first_zip_member(body)
        if self.file_format != "yaml":
            return json.loads(body)
        try:
            import yaml  # PyYAML, only needed for the CryptoScamDB lists
        except ImportError as exc:
            raise ValueError("PyYAML is not installed (pip install -r requirements.txt)") from exc
        loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)  # the C parser is much faster when present
        try:
            return yaml.load(body, Loader=loader)  # noqa: S506 - a safe loader
        except yaml.YAMLError as exc:
            raise ValueError(f"not valid YAML: {exc}") from exc


def _parse_address_list(data: object) -> list[Record]:
    if not isinstance(data, list) or not data:
        raise SnapshotRejected("expected a non-empty JSON list of addresses")
    if not all(isinstance(item, str) and item.strip() for item in data):
        raise SnapshotRejected("every entry should be a non-empty address string")
    return [Record(address=item.strip()) for item in data]


def _parse_time(value: object) -> datetime | None:
    if isinstance(value, (int, float)) and value > 0:
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _parse_ransomwhere(data: object) -> list[Record]:
    rows = data.get("result") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        raise SnapshotRejected('expected {"result": [...]} with at least one address')
    records = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("address"), str):
            raise SnapshotRejected("every result should be an object with an address")
        if row.get("blockchain", "bitcoin") != "bitcoin":
            continue
        times = [t for t in (_parse_time(tx.get("time")) for tx in row.get("transactions") or []
                             if isinstance(tx, dict)) if t]
        records.append(Record(
            address=row["address"].strip(),
            family=row.get("family") or None,
            first_seen=min(times) if times else None,
            last_seen=max(times) if times else None,
            payment_count=len(row.get("transactions") or []),
            listed_at=_parse_time(row.get("createdAt")),
        ))
    if not records:
        raise SnapshotRejected("no Bitcoin addresses in the export")
    return records


def _ofac(code: str, chain: str, title: str) -> IndicatorSource:
    filename = f"sanctioned_addresses_{code}.json"
    return IndicatorSource(
        id=f"ofac_{code.lower()}",
        title=title,
        url=OFAC_RAW + filename,
        chain=chain,
        label="on the OFAC sanctions (SDN) list",
        licence="OFAC data is US government work; extractor and lists MIT (0xB10C)",
        parse=_parse_address_list,
        commits_url=f"https://api.github.com/repos/{OFAC_REPO}/commits?sha=lists&path={filename}&per_page=1",
        note="Community extraction of OFAC's SDN list; confirm important matches on sanctionssearch.ofac.treas.gov.",
    )


def _site_records(entry: dict, category: str | None, addresses: tuple, description: str | None,
                  pages: bool) -> list[Record]:
    """Records for one website entry: its host from both `name` and `url` (they sometimes differ by a
    typo). With `pages`, an entry that names a path lists only that page (one post on medium.com or
    t.me must not flag the whole host); without, paths are dropped and the whole host counts."""
    splits = [split_url(t) for t in (entry.get("name"), entry.get("url")) if isinstance(t, str)]
    splits = [s for s in splits if s]
    if pages and any(path for _, path in splits):
        splits = [s for s in splits if s[1]]
    elif not pages:
        splits = [(host, "") for host, _ in splits]
    return [Record(address=host, path=path, family=category, addresses=addresses, description=description)
            for host, path in dict.fromkeys(splits)]


def _csdb_addresses(entry: dict) -> tuple[tuple[str, str], ...]:
    found = entry.get("addresses")
    if not isinstance(found, dict):
        return ()
    return tuple((str(code).upper(), str(a).strip()) for code, items in found.items()
                 for a in (items or []) if isinstance(a, (str, int)) and str(a).strip())


def _parse_cryptoscamdb(data: object, minimum: int, kind: str) -> list[Record]:
    if not isinstance(data, list) or len(data) < minimum:
        raise SnapshotRejected(f"expected a YAML list of at least {minimum} {kind} entries")
    records = []
    for entry in data:
        if not isinstance(entry, dict) or not isinstance(entry.get("url") or entry.get("name"), str):
            raise SnapshotRejected("every entry should have a name or url")
        if kind == "blacklist":
            category = " / ".join(str(v) for v in (entry.get("category"), entry.get("subcategory")) if v) or None
        else:
            category = str(entry.get("name")) if entry.get("name") else None
        description = entry.get("description") if isinstance(entry.get("description"), str) else None
        # Whitelist addresses (donation addresses of genuine projects) are not indicators, so drop them.
        addresses = _csdb_addresses(entry) if kind == "blacklist" else ()
        records.extend(_site_records(entry, category, addresses, description, pages=kind == "blacklist"))
    if not records:
        raise SnapshotRejected("no usable website names in the list")
    return records


def _parse_csdb_blacklist(data: object) -> list[Record]:
    return _parse_cryptoscamdb(data, 100, "blacklist")


def _parse_csdb_whitelist(data: object) -> list[Record]:
    return _parse_cryptoscamdb(data, 10, "whitelist")


def _cryptoscamdb(kind: str, title: str, label: str, parse, note: str) -> IndicatorSource:
    repo = f"CryptoScamDB/{kind}"
    return IndicatorSource(
        id=f"cryptoscamdb_{kind}",
        title=title,
        url=f"https://raw.githubusercontent.com/{repo}/master/data/urls.yaml",
        chain="web",
        label=label,
        licence="No licence given (all rights reserved by default): local lookups only, do not redistribute",
        parse=parse,
        commits_url=f"https://api.github.com/repos/{repo}/commits?path=data/urls.yaml&per_page=1",
        note=note,
        file_format="yaml",
        use_for_training=False,
        site_role=BLOCK if kind == "blacklist" else ALLOW,
    )


MAX_UNZIPPED = 200 * 1024 * 1024  # refuse a zip that unpacks to more than this


def _first_zip_member(body: bytes) -> bytes:
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            members = [m for m in archive.infolist() if not m.is_dir()]
            if not members:
                raise ValueError("the zip file is empty")
            if members[0].file_size > MAX_UNZIPPED:
                raise ValueError(f"the zip file unpacks to {members[0].file_size:,} bytes, more than expected")
            return archive.read(members[0])
    except zipfile.BadZipFile as exc:
        raise ValueError(f"not a valid zip file: {exc}") from exc


# A plain ASCII host name. Anything else (a path, port or international letters) goes through split_url.
_PLAIN_HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}")


def clean_site(entry: object) -> tuple[str, str] | None:
    """(host, path) for one list entry, normalised like validation.split_url, or None if unusable.

    "*.example.com" counts as example.com. Plain names skip split_url, which is slow for lists of
    hundreds of thousands of names.
    """
    if not isinstance(entry, str):
        return None
    text = entry.strip().rstrip(".")
    if text.startswith("*."):
        text = text[2:]
    if not text or text.startswith("#"):
        return None
    host = text.lower()
    if _PLAIN_HOST.fullmatch(host):
        return (host[4:] if host.startswith("www.") and host.count(".") > 1 else host), ""
    return split_url(text)  # keeps the letter case of a page path, as lookups do


def _site_list(entries: object, minimum: int, what: str, pages: bool = True, **extra) -> SiteList:
    """A SiteList from a list of names. An entry with a path becomes a single listed page (or is
    skipped without `pages`): one listed page must never flag a whole shared host."""
    if not isinstance(entries, list) or len(entries) < minimum:
        raise SnapshotRejected(f"expected a list of at least {minimum:,} {what}")
    hosts, page_map, skipped = set(), {}, 0
    for entry in entries:
        split = clean_site(entry)
        if split is None or (split[1] and not pages):
            skipped += 1
        elif split[1]:
            page_map.setdefault(split[0], set()).add(split[1])
        else:
            hosts.add(split[0])
    if len(hosts) + len(page_map) < minimum:
        raise SnapshotRejected(f"only {len(hosts):,} usable {what}, expected at least {minimum:,}")
    return SiteList(frozenset(hosts), {h: tuple(sorted(p)) for h, p in page_map.items()},
                    skipped=skipped, **extra)


def _metamask_field(data: object, key: str) -> list:
    if not isinstance(data, dict) or not all(isinstance(data.get(k), list)
                                             for k in ("blacklist", "whitelist", "fuzzylist")):
        raise SnapshotRejected("expected MetaMask's config.json with blacklist, whitelist and fuzzylist lists")
    return data[key]


def _parse_metamask_blacklist(data: object) -> SiteList:
    return _site_list(_metamask_field(data, "blacklist"), 1000, "blacklisted websites")


def _parse_metamask_whitelist(data: object) -> SiteList:
    targets = tuple(split[0] for split in map(clean_site, _metamask_field(data, "fuzzylist"))
                    if split and not split[1])
    return _site_list(_metamask_field(data, "whitelist"), 10, "whitelisted websites", lookalike_targets=targets)


def _parse_scamsniffer(data: object) -> SiteList:
    return _site_list(data, 1000, "scam website names")


def _polkadot_field(data: object, key: str) -> list:
    if not isinstance(data, dict) or not isinstance(data.get("allow"), list) or not isinstance(data.get("deny"), list):
        raise SnapshotRejected("expected Polkadot's all.json with allow and deny lists")
    if key == "deny":  # denySub: single pages on big sites, e.g. twitter.com/<fake account>
        return data["deny"] + [e for e in data.get("denySub") or [] if isinstance(e, str) and "/" in e]
    return data[key]


def _parse_polkadot_deny(data: object) -> SiteList:
    return _site_list(_polkadot_field(data, "deny"), 1000, "denied websites")


def _parse_polkadot_allow(data: object) -> SiteList:
    return _site_list(_polkadot_field(data, "allow"), 10, "shared hosting platforms", pages=False)


def _parse_phishing_database(data: object) -> SiteList:
    if not isinstance(data, str):
        raise SnapshotRejected("expected a text file with one domain per line")
    return _site_list(data.splitlines(), 1000, "phishing domains")


TRANCO_TOP = 100_000
TRANCO_MINIMUM = 10_000  # fewer usable rows than this means the file is not the Tranco list


def _parse_tranco(data: object) -> SiteList:
    """The first TRANCO_TOP rows of Tranco's rank,domain CSV."""
    if not isinstance(data, bytes):
        raise SnapshotRejected("expected the Tranco CSV inside the zip")
    ranks: dict[str, int] = {}
    for line in io.BytesIO(data):
        rank_text, _, name = line.decode("utf-8", "replace").strip().partition(",")
        if not rank_text.isdigit():
            raise SnapshotRejected(f"expected rank,domain rows, got {line[:60]!r}")
        rank = int(rank_text)
        if rank > TRANCO_TOP:
            break
        split = clean_site(name)
        if split and not split[1]:
            ranks.setdefault(split[0], rank)
    if len(ranks) < TRANCO_MINIMUM:
        raise SnapshotRejected(f"only {len(ranks):,} ranked domains, expected about {TRANCO_TOP:,}")
    return SiteList(frozenset(ranks), ranks=ranks)


def _github_list(repo: str, branch: str, path: str) -> dict:
    return {"url": f"https://raw.githubusercontent.com/{repo}/{branch}/{path}",
            "commits_url": f"https://api.github.com/repos/{repo}/commits?sha={branch}&path={path}&per_page=1"}


METAMASK = ("MetaMask/eth-phishing-detect", "main", "src/config.json")
POLKADOT = ("polkadot-js/phishing", "master", "all.json")
METAMASK_LICENCE = "DBAD Public License 1.2 (permissive), MetaMask/eth-phishing-detect"
POLKADOT_LICENCE = "Apache-2.0, polkadot-js/phishing"


SOURCES: dict[str, IndicatorSource] = {
    source.id: source
    for source in (
        _ofac("XBT", "bitcoin", "OFAC sanctioned Bitcoin addresses"),
        _ofac("ETH", "evm", "OFAC sanctioned Ethereum-format addresses"),
        _ofac("SOL", "solana", "OFAC sanctioned Solana addresses"),
        IndicatorSource(
            id="ransomwhere",
            title="Ransomwhere ransomware payment addresses",
            url="https://api.ransomwhe.re/export",
            chain="bitcoin",
            label="a reported ransomware payment address (Ransomwhere)",
            licence="Crowdsourced; check the Zenodo dataset record before redistributing",
            parse=_parse_ransomwhere,
            note="Addresses are published 90 days after submission and the list is not complete.",
        ),
        _cryptoscamdb(
            "blacklist", "CryptoScamDB scam website blacklist",
            "on the CryptoScamDB scam website blacklist", _parse_csdb_blacklist,
            "Community-reported scam and phishing sites with some of their payment addresses; "
            "not updated since mid-2022, so newer scams are missing.",
        ),
        _cryptoscamdb(
            "whitelist", "CryptoScamDB genuine website whitelist",
            "on the CryptoScamDB whitelist of genuine crypto websites", _parse_csdb_whitelist,
            "A short list (about 90) of genuine crypto sites, last changed in 2021; "
            "being listed is not a guarantee the site is safe today.",
        ),
        IndicatorSource(
            id="metamask_phishing", title="MetaMask phishing blacklist", **_github_list(*METAMASK),
            chain="web", site_role=BLOCK, label="on MetaMask's phishing blacklist (eth-phishing-detect)",
            licence=METAMASK_LICENCE, parse=_parse_metamask_blacklist, use_for_training=False,
            note="Crypto phishing sites blocked by the MetaMask wallet; updated several times a day.",
        ),
        IndicatorSource(
            id="metamask_whitelist", title="MetaMask whitelist", **_github_list(*METAMASK),
            chain="web", site_role=ALLOW,
            label="on MetaMask's whitelist (reviewed and exempted from its look-alike check)",
            licence=METAMASK_LICENCE, parse=_parse_metamask_whitelist, use_for_training=False,
            note="About 60 names MetaMask reviewed and does not treat as look-alikes, plus the 8 names it checks "
                 "look-alikes against. Some are genuine sites, others only names MetaMask chose not to block.",
        ),
        IndicatorSource(
            id="scamsniffer_domains", title="ScamSniffer scam domain blacklist",
            **_github_list("scamsniffer/scam-database", "main", "blacklist/domains.json"),
            chain="web", site_role=BLOCK, label="on ScamSniffer's crypto scam domain blacklist",
            licence="GPL-3.0, scamsniffer/scam-database", parse=_parse_scamsniffer, use_for_training=False,
            note="Crypto drainer and phishing domains; the public copy is published with a 7-day delay.",
        ),
        IndicatorSource(
            id="polkadot_phishing", title="Polkadot{.js} phishing deny list", **_github_list(*POLKADOT),
            chain="web", site_role=BLOCK, label="on the Polkadot{.js} phishing deny list",
            licence=POLKADOT_LICENCE, parse=_parse_polkadot_deny, use_for_training=False,
            note="Crypto phishing sites (Polkadot and Ethereum ecosystems) blocked by the Polkadot{.js} extension.",
        ),
        IndicatorSource(
            id="polkadot_allowlist", title="Polkadot{.js} shared hosting allow list", **_github_list(*POLKADOT),
            chain="web", site_role=SHARED, label="a shared hosting platform where anyone can publish a site",
            licence=POLKADOT_LICENCE, parse=_parse_polkadot_allow, use_for_training=False,
            note="About 30 platforms such as github.io and vercel.app, which Polkadot never blocks as a whole. Here "
                 "they stop a listing or the popularity of a platform from counting for every site on it.",
        ),
        IndicatorSource(
            id="phishing_database", title="Phishing.Database active phishing domains",
            **_github_list("mitchellkrogza/Phishing.Database", "master", "phishing-domains-ACTIVE.txt"),
            chain="web", site_role=BLOCK, crypto_specific=False,
            label="on Phishing.Database's list of active phishing domains (general, not crypto-specific)",
            licence="MIT, mitchellkrogza/Phishing.Database", parse=_parse_phishing_database,
            file_format="text", use_for_training=False,
            note="General phishing of every kind (banks, mail, shops), checked for being active; weighted lower "
                 "than the crypto lists and has some false positives.",
        ),
        IndicatorSource(
            id="tranco_top", title="Tranco top 100,000 websites", url="https://tranco-list.eu/top-1m.csv.zip",
            chain="web", site_role=POPULAR, label="among the 100,000 most popular websites (Tranco)",
            licence="Tranco list (tranco-list.eu); its inputs include CC BY-NC data: internal, non-commercial use only",
            parse=_parse_tranco, file_format="zip", use_for_training=False,
            note="Popularity only: popular sites can host scams too, shared hosts are ignored and blacklists always win.",
        ),
    )
}
