"""The Sources page: data, services and software this project relies on, with credit and licence notes.

The list below is copied into the database the first time it is read. After that the database copy
is the one shown, and admins change it from the Sources page.
"""
from __future__ import annotations

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

CATEGORIES = (
    "Live blockchain data",
    "Sanctions and threat intelligence",
    "Research datasets",
    "Infrastructure",
    "Open-source software",
)

DEFAULT_SOURCES: list[dict] = [
    {
        "name": "mempool.space REST API",
        "category": "Live blockchain data",
        "url": "https://mempool.space/docs/api/rest",
        "used_for": "First choice for live Bitcoin address and transaction lookups, and block statistics.",
        "licence": "Free public API with rate limits; mempool software is AGPL-3.0.",
        "citation": "The Mempool Open Source Project, mempool.space.",
    },
    {
        "name": "Blockstream Esplora API (blockstream.info)",
        "category": "Live blockchain data",
        "url": "https://github.com/Blockstream/esplora/blob/master/API.md",
        "used_for": "Backup for live Bitcoin lookups when mempool.space is rate limited or down.",
        "licence": "Free public API; Esplora software is MIT.",
        "citation": "Blockstream, Esplora block explorer.",
    },
    {
        "name": "Etherscan API V2",
        "category": "Live blockchain data",
        "url": "https://docs.etherscan.io/",
        "used_for": "Live Ethereum address and transaction lookups (balances, transactions, ERC-20 transfers).",
        "licence": "Free API key: 3 calls per second, 100,000 per day; Etherscan terms of service apply.",
        "citation": "Etherscan, Ethereum blockchain explorer API (etherscan.io).",
    },
    {
        "name": "OFAC Specially Designated Nationals (SDN) list",
        "category": "Sanctions and threat intelligence",
        "url": "https://sanctionssearch.ofac.treas.gov/",
        "used_for": "Exact-match screening of Bitcoin, Ethereum-format and Solana addresses against US sanctions.",
        "licence": "US government work. Addresses extracted nightly by 0xB10C/ofac-sanctioned-digital-currency-addresses (MIT).",
        "citation": "U.S. Department of the Treasury, Office of Foreign Assets Control; extraction by 0xB10C.",
    },
    {
        "name": "Ransomwhere",
        "category": "Sanctions and threat intelligence",
        "url": "https://ransomwhe.re/",
        "used_for": "Exact-match screening against reported ransomware payment addresses, and training labels.",
        "licence": "Crowdsourced data; check the Zenodo dataset record before redistributing.",
        "citation": "Cable, J. et al., Ransomwhere: a crowdsourced ransomware payment dataset (Zenodo).",
    },
    {
        "name": "Google BigQuery public dataset crypto_bitcoin",
        "category": "Research datasets",
        "url": "https://console.cloud.google.com/marketplace/product/bitcoin/crypto-bitcoin",
        "used_for": "Historical blocks and transactions for the training dataset (wallet-window features).",
        "licence": "Public dataset; query costs follow Google Cloud BigQuery pricing.",
        "citation": "Google Cloud, bigquery-public-data.crypto_bitcoin.",
    },
    {
        "name": "Elliptic++ dataset",
        "category": "Research datasets",
        "url": "https://github.com/git-disl/EllipticPlusPlus",
        "used_for": "Labelled illicit and licit Bitcoin transactions and wallet addresses for model training.",
        "licence": "No licence stated in the repository; confirm permitted use with your supervisor.",
        "citation": "Elmougy, Y. and Liu, L. (2023). Demystifying Fraudulent Transactions and Illicit Nodes in the "
                    "Bitcoin Network for Financial Forensics. KDD '23.",
    },
    {
        "name": "Neon serverless Postgres",
        "category": "Infrastructure",
        "url": "https://neon.tech/",
        "used_for": "Stores lookups, investigations, cases, user accounts and the audit log.",
        "licence": "Managed service (Neon terms of service).",
        "citation": "",
    },
    {
        "name": "Cytoscape.js",
        "category": "Open-source software",
        "url": "https://js.cytoscape.org/",
        "used_for": "Draws the interactive transaction and address graph.",
        "licence": "MIT",
        "citation": "Franz, M. et al. (2016). Cytoscape.js: a graph theory library for visualisation and analysis. "
                    "Bioinformatics 32(2).",
    },
    {
        "name": "FastAPI, React and Vite",
        "category": "Open-source software",
        "url": "https://fastapi.tiangolo.com/",
        "used_for": "The backend API (FastAPI) and the dashboard in the browser (React, built with Vite).",
        "licence": "MIT",
        "citation": "",
    },
]

_COLUMNS = "id, name, category, url, used_for, licence, citation, position, updated_at"
EDITABLE = ("name", "category", "url", "used_for", "licence", "citation")


def ensure_seeded(pool: ConnectionPool) -> None:
    """Copy the default list into the database once. Deleting every source later does not bring them back."""
    with pool.connection() as conn:
        first_time = conn.execute(
            "INSERT INTO app_meta (key, value) VALUES ('sources_seeded', now()::text) ON CONFLICT DO NOTHING RETURNING key"
        ).fetchone()
        if not first_time:
            _add_later_defaults(conn)
            return
        conn.execute("INSERT INTO app_meta (key, value) SELECT 'source_added:' || name, now()::text "
                     "FROM unnest(%s::text[]) AS name ON CONFLICT DO NOTHING", (list(ADDED_LATER),))
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO data_sources (name, category, url, used_for, licence, citation, position)
                   VALUES (%(name)s, %(category)s, %(url)s, %(used_for)s, %(licence)s, %(citation)s, %(position)s)""",
                [{**source, "position": index} for index, source in enumerate(DEFAULT_SOURCES)],
            )


# Defaults added after a database may already have been seeded. Each is added once; if an admin
# deletes it afterwards it stays deleted.
ADDED_LATER = ("Etherscan API V2",)


def _add_later_defaults(conn) -> None:
    for name in ADDED_LATER:
        first = conn.execute("INSERT INTO app_meta (key, value) VALUES (%s, now()::text) ON CONFLICT DO NOTHING "
                             "RETURNING key", (f"source_added:{name}",)).fetchone()
        if first:
            source = next(s for s in DEFAULT_SOURCES if s["name"] == name)
            conn.execute(
                """INSERT INTO data_sources (name, category, url, used_for, licence, citation, position)
                   VALUES (%(name)s, %(category)s, %(url)s, %(used_for)s, %(licence)s, %(citation)s,
                           (SELECT coalesce(max(position), 0) + 1 FROM data_sources))""", source)


def list_sources(pool: ConnectionPool) -> list[dict]:
    ensure_seeded(pool)
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""SELECT s.id, s.name, s.category, s.url, s.used_for, s.licence, s.citation, s.position,
                              s.updated_at, u.display_name AS updated_by_name
                       FROM data_sources s LEFT JOIN app_users u ON u.id = s.updated_by
                       ORDER BY s.position, s.id""")
        return cur.fetchall()


def create_source(pool: ConnectionPool, user_id: int, fields: dict) -> int:
    with pool.connection() as conn:
        position = conn.execute("SELECT coalesce(max(position), -1) + 1 FROM data_sources").fetchone()[0]
        return conn.execute(
            """INSERT INTO data_sources (name, category, url, used_for, licence, citation, position, updated_by)
               VALUES (%(name)s, %(category)s, %(url)s, %(used_for)s, %(licence)s, %(citation)s, %(position)s, %(user_id)s)
               RETURNING id""",
            {**fields, "position": position, "user_id": user_id},
        ).fetchone()[0]


def update_source(pool: ConnectionPool, source_id: int, user_id: int, fields: dict) -> dict | None:
    """Apply the changes and return the row as it was before, or None if there is no such source."""
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT {_COLUMNS} FROM data_sources WHERE id = %s FOR UPDATE", (source_id,))
        before = cur.fetchone()
        if before is None:
            return None
        sets = ", ".join(f"{column} = %({column})s" for column in fields)
        cur.execute(f"UPDATE data_sources SET {sets}, updated_by = %(user_id)s, updated_at = now() WHERE id = %(id)s",
                    {**fields, "user_id": user_id, "id": source_id})
        return before


def delete_source(pool: ConnectionPool, source_id: int) -> dict | None:
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("DELETE FROM data_sources WHERE id = %s RETURNING id, name", (source_id,))
        return cur.fetchone()


def move_source(pool: ConnectionPool, source_id: int, direction: int) -> bool:
    """Swap a source with its neighbour above (-1) or below (+1)."""
    with pool.connection() as conn:
        rows = conn.execute("SELECT id FROM data_sources ORDER BY position, id").fetchall()
        ids = [row[0] for row in rows]
        if source_id not in ids:
            return False
        index = ids.index(source_id)
        other = index + direction
        if 0 <= other < len(ids):
            ids[index], ids[other] = ids[other], ids[index]
        with conn.cursor() as cur:
            cur.executemany("UPDATE data_sources SET position = %s WHERE id = %s", list(enumerate(ids)))
        return True
