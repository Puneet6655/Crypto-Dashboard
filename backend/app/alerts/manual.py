"""Addresses people add to the live-alert watchlist by hand (Alerts page), one list per chain.

Each chain has its own cap, set by what watching costs:
- Ethereum: added to Alchemy's filtered WebSocket subscription (500 addresses at most, shared with case and
  OFAC addresses; hand-added and case addresses go first). Cost follows activity, not list size.
- Solana: one getSignaturesForAddress call (40 CU) per address per round, at most one call every 30 seconds
  in total, so the cost is capped at about 3.5 million CU a month (the free plan has 30 million).
- Bitcoin: polled from mempool.space (blockstream.info as backup), which costs no Alchemy units.
"""
from __future__ import annotations

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.validation import (
    is_bitcoin_address,
    is_ethereum_address,
    is_solana_address,
    normalize_address,
    normalize_hex,
)

CHAINS = ("ethereum", "solana", "bitcoin")
MAX_PER_CHAIN = {"ethereum": 100, "solana": 20, "bitcoin": 50}
REASON = "watchlist"

_PROBLEMS = {
    "ethereum": "An Ethereum address is 0x followed by 40 hexadecimal characters.",
    "solana": "A Solana address is 32 to 44 base58 characters (no 0, O, I or l) that decode to 32 bytes.",
    "bitcoin": "A Bitcoin address starts with 1 or 3 (26 to 35 characters) or bc1q / bc1p (SegWit or Taproot).",
}

_TABLE = """CREATE TABLE IF NOT EXISTS alert_watch_addresses (
    id BIGSERIAL PRIMARY KEY,
    chain TEXT NOT NULL,
    address TEXT NOT NULL,
    label TEXT,
    added_by_email TEXT,
    added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (chain, address)
)"""
_ready: set[int] = set()


def clean(chain: str, address: str) -> tuple[str | None, str | None]:
    """(address in its one spelling, None) or (None, why it is not valid for that chain)."""
    value = (address or "").strip()
    if chain not in CHAINS:
        return None, f"Live alerts watch {', '.join(CHAINS)}."
    if not value:
        return None, "Enter an address."
    if chain == "ethereum" and is_ethereum_address(value):
        return normalize_hex(value), None
    if chain == "solana" and is_solana_address(value):
        return value, None
    if chain == "bitcoin" and is_bitcoin_address(value):
        return normalize_address(value), None
    return None, _PROBLEMS[chain]


def reason(label: str | None) -> str:
    return f"{REASON}: {label}"[:120] if label else REASON


def _ensure(pool: ConnectionPool) -> None:
    if id(pool) in _ready:
        return
    with pool.connection() as conn:
        conn.execute(_TABLE)
    _ready.add(id(pool))


def list_all(pool: ConnectionPool) -> list[dict]:
    _ensure(pool)
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT id, chain, address, label, added_by_email, added_at FROM alert_watch_addresses "
                    "ORDER BY added_at DESC")
        return cur.fetchall()


def load_chain(pool: ConnectionPool, chain: str) -> dict[str, list[str]]:
    """address -> [reason] for one chain, newest first."""
    _ensure(pool)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT address, label FROM alert_watch_addresses WHERE chain = %s ORDER BY added_at DESC",
                    (chain,))
        return {address: [reason(label)] for address, label in cur.fetchall()}


class WatchlistError(ValueError):
    """Shown to the user as it is."""


def add(pool: ConnectionPool, chain: str, address: str, label: str | None, email: str | None) -> dict:
    value, problem = clean(chain, address)
    if problem:
        raise WatchlistError(problem)
    label = (label or "").strip()[:100] or None
    _ensure(pool)
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT count(*) AS n FROM alert_watch_addresses WHERE chain = %s", (chain,))
        if cur.fetchone()["n"] >= MAX_PER_CHAIN[chain]:
            raise WatchlistError(f"The {chain} watchlist is full ({MAX_PER_CHAIN[chain]} addresses). "
                                 "Remove one first.")
        cur.execute("""INSERT INTO alert_watch_addresses (chain, address, label, added_by_email)
                       VALUES (%s, %s, %s, %s) ON CONFLICT (chain, address) DO NOTHING
                       RETURNING id, chain, address, label, added_by_email, added_at""",
                    (chain, value, label, email))
        row = cur.fetchone()
    if row is None:
        raise WatchlistError("That address is already on the watchlist.")
    return row


def remove(pool: ConnectionPool, item_id: int) -> dict | None:
    _ensure(pool)
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("DELETE FROM alert_watch_addresses WHERE id = %s RETURNING id, chain, address, label",
                    (item_id,))
        return cur.fetchone()
