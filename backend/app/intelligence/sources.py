"""The indicator lists we download, and the checks each file must pass before it replaces the old one.

All four need no account or key (see the data source guide, sections 2 and 4):

* OFAC sanctioned addresses for Bitcoin (XBT), Ethereum-format (ETH) and Solana (SOL), as
  extracted nightly from OFAC's SDN list by github.com/0xB10C/ofac-sanctioned-digital-currency-addresses
  (MIT). A community copy of the official list: verify important hits against OFAC itself.
* Ransomwhere (ransomwhe.re): crowdsourced Bitcoin ransomware payment addresses with family
  and payment times. Addresses appear 90 days after submission; check the Zenodo record before
  sharing the data further.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

OFAC_REPO = "0xB10C/ofac-sanctioned-digital-currency-addresses"
OFAC_RAW = f"https://raw.githubusercontent.com/{OFAC_REPO}/lists/"


class SnapshotRejected(ValueError):
    """The downloaded file does not look like the list we expect, so the old snapshot is kept."""


@dataclass(frozen=True)
class Record:
    """One listed address, as stored in the index and the labels CSV."""

    address: str
    family: str | None = None  # ransomware family, when the source names one
    first_seen: datetime | None = None  # earliest payment the source lists
    last_seen: datetime | None = None
    payment_count: int | None = None
    listed_at: datetime | None = None  # when the source added the address


@dataclass(frozen=True)
class IndicatorSource:
    id: str
    title: str
    url: str
    chain: str  # "bitcoin", "evm" or "solana": decides how addresses are compared
    label: str  # what a match means, in plain words
    licence: str
    parse: Callable[[object], list[Record]]
    # GitHub API URL whose newest commit date says when the list file last changed.
    commits_url: str | None = None
    note: str | None = None


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
    )
}
