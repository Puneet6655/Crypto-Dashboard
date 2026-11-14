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
        "licence": "Free public API, rate limited, provided as is under the mempool.space Terms of Service "
                   "(updated 10 July 2024); no endorsement. Mempool software is AGPL-3.0.",
        "citation": "The Mempool Open Source Project, mempool.space.",
    },
    {
        "name": "Blockstream Esplora API (blockstream.info)",
        "category": "Live blockchain data",
        "url": "https://github.com/Blockstream/esplora/blob/master/API.md",
        "used_for": "Backup for live Bitcoin lookups when mempool.space is rate limited or down.",
        "licence": "Free public API, rate limited, provided as is; Blockstream's terms apply. Esplora software is MIT.",
        "citation": "Blockstream, Esplora block explorer.",
    },
    {
        "name": "Etherscan API V2",
        "category": "Live blockchain data",
        "url": "https://etherscan.io/apiterms",
        "used_for": "Live Ethereum address and transaction lookups (balances, transactions, ERC-20 transfers). "
                    "Powered by Etherscan.io APIs.",
        "licence": "Etherscan API Terms (free key: personal and research use, 3 calls/s, 100,000/day). Attribution "
                   "\"Powered by Etherscan.io APIs\" required; no ML training on its data; no bulk storage or "
                   "redistribution; short cache only.",
        "citation": "Etherscan, Ethereum blockchain explorer API (etherscan.io).",
    },
    {
        "name": "Blockscout PRO API",
        "category": "Live blockchain data",
        "url": "https://docs.blockscout.com/devs/pro-api",
        "used_for": "Backup for live Ethereum lookups when Etherscan is rate limited or down (same account API, "
                    "tried before Alchemy), and live USDC transfers on Base for x402 checks (base.blockscout.com "
                    "public explorer API, or the PRO API when BLOCKSCOUT_API_KEY is set).",
        "licence": "Free API key: 5 requests per second, 100,000 credits per day; Blockscout terms of service apply. "
                   "Blockscout explorer software is GPL-3.0.",
        "citation": "Blockscout, open-source blockchain explorer and PRO API (blockscout.com).",
    },
    {
        "name": "Alchemy API",
        "category": "Live blockchain data",
        "url": "https://www.alchemy.com/docs",
        "used_for": "Solana lookups (balances, SPL tokens, transactions), Base lookups, the Ethereum backup when "
                    "Etherscan is rate limited or down, token holdings and NFTs, and live alerts over its WebSocket. "
                    "On only when ALCHEMY_ENABLED=true.",
        "licence": "Alchemy Terms of Service v2.0 (free plan, 30 million compute units a month, provided as is). "
                   "No attribution required; no scraping or bulk mining of data; one account only.",
        "citation": "Alchemy Insights, Inc., Alchemy blockchain APIs (alchemy.com).",
    },
    {
        "name": "OFAC Specially Designated Nationals (SDN) list",
        "category": "Sanctions and threat intelligence",
        "url": "https://sanctionssearch.ofac.treas.gov/",
        "used_for": "Exact-match screening of Bitcoin, Ethereum-format and Solana addresses against US sanctions.",
        "licence": "US government work (public domain). Addresses extracted nightly by "
                   "0xB10C/ofac-sanctioned-digital-currency-addresses (MIT).",
        "citation": "U.S. Department of the Treasury, Office of Foreign Assets Control; extraction by 0xB10C.",
    },
    {
        "name": "Ransomwhere",
        "category": "Sanctions and threat intelligence",
        "url": "https://ransomwhe.re/",
        "used_for": "Exact-match screening against reported ransomware payment addresses, and training labels.",
        "licence": "CC BY 4.0 (Zenodo record 13999026): free to use and share with attribution.",
        "citation": "Cable, J. (2024). Ransomwhere: A Crowdsourced Ransomware Payment Dataset. Zenodo. "
                    "https://doi.org/10.5281/zenodo.13999026",
    },
    {
        "name": "CryptoScamDB",
        "category": "Sanctions and threat intelligence",
        "url": "https://github.com/CryptoScamDB",
        "used_for": "Scam website blacklist (with reported payment addresses) and whitelist of genuine crypto sites, "
                    "for website checks. Local lookups only; not updated since 2022.",
        "licence": "No licence given (all rights reserved): local lookups only, not redistributed or used for training.",
        "citation": "CryptoScamDB, github.com/CryptoScamDB/blacklist and /whitelist.",
    },
    {
        "name": "MetaMask eth-phishing-detect",
        "category": "Sanctions and threat intelligence",
        "url": "https://github.com/MetaMask/eth-phishing-detect",
        "used_for": "Crypto phishing website blacklist, whitelist and look-alike names, for website checks.",
        "licence": "DBAD Public License 1.2 (permissive). Not used for training.",
        "citation": "MetaMask, eth-phishing-detect.",
    },
    {
        "name": "ScamSniffer scam database",
        "category": "Sanctions and threat intelligence",
        "url": "https://github.com/scamsniffer/scam-database",
        "used_for": "Crypto drainer and phishing domain blacklist (public copy delayed 7 days), for website checks.",
        "licence": "GPL-3.0: used unmodified for local lookups; not redistributed. Not used for training.",
        "citation": "ScamSniffer, scam-database.",
    },
    {
        "name": "Polkadot{.js} phishing list",
        "category": "Sanctions and threat intelligence",
        "url": "https://github.com/polkadot-js/phishing",
        "used_for": "Crypto phishing deny list, and its allow list of shared hosting platforms, for website checks.",
        "licence": "Apache-2.0. Not used for training.",
        "citation": "Polkadot{.js}, phishing.",
    },
    {
        "name": "Phishing.Database",
        "category": "Sanctions and threat intelligence",
        "url": "https://github.com/mitchellkrogza/Phishing.Database",
        "used_for": "General (not crypto-specific) list of active phishing domains, weighted lower in website checks.",
        "licence": "MIT. Not used for training.",
        "citation": "Krog, M. and Ganz, N., Phishing.Database.",
    },
    {
        "name": "Dune cex.addresses",
        "category": "Sanctions and threat intelligence",
        "url": "https://docs.dune.com/data-catalog/curated/cex-flows/addresses",
        "used_for": "Names known exchange addresses in the Graph explorer (community-curated Spellbook seed lists from "
                    "GitHub, with the Dune API as backup).",
        "licence": "Business Source License 1.1 until 3 March 2027 (then GPL-3.0): no use in a data or analytics "
                   "platform offered to third parties. Own analysis only; snapshot not committed.",
        "citation": "Dune Analytics (2026). Spellbook: cex.addresses. https://github.com/duneanalytics/spellbook",
    },
    {
        "name": "x402analysis",
        "category": "Sanctions and threat intelligence",
        "url": "https://github.com/drinkcoffee/x402analysis",
        "used_for": "x402 servers, services, clients, funders and phishing tags, shipped as a snapshot in "
                    "backend/data/x402, for x402 payment checks. Not used for model training.",
        "licence": "MIT",
        "citation": "Robinson, P., x402analysis. https://github.com/drinkcoffee/x402analysis",
    },
    {
        "name": "x402scan facilitator list",
        "category": "Sanctions and threat intelligence",
        "url": "https://github.com/Merit-Systems/x402scan",
        "used_for": "Addresses of x402 payment facilitators, to recognise x402 payments. Not used for model training.",
        "licence": "MIT",
        "citation": "Merit Systems, x402scan. https://github.com/Merit-Systems/x402scan",
    },
    {
        "name": "Coinbase CDP SQL API",
        "category": "Live blockchain data",
        "url": "https://docs.cdp.coinbase.com/data/sql-api/welcome",
        "used_for": "Optional: every x402 payment on Base. Used only when CDP_API_KEY_ID and CDP_API_KEY_SECRET are "
                    "set. Not used for model training.",
        "licence": "Coinbase Developer Platform terms of service apply (free tier with usage limits).",
        "citation": "Coinbase Developer Platform, CDP SQL API.",
    },
    {
        "name": "Google BigQuery public dataset crypto_bitcoin",
        "category": "Research datasets",
        "url": "https://console.cloud.google.com/marketplace/product/bitcoin/crypto-bitcoin",
        "used_for": "Historical blocks and transactions for the training dataset (wallet-window and multi-hop features).",
        "licence": "Google Cloud Public Datasets, provided as is under the Google Cloud terms; the underlying Bitcoin "
                   "blockchain data is public. Queries billed by BigQuery pricing (1 TB a month free).",
        "citation": "Google Cloud, bigquery-public-data.crypto_bitcoin (blockchain-etl).",
    },
    {
        "name": "Elliptic++ dataset",
        "category": "Research datasets",
        "url": "https://github.com/git-disl/EllipticPlusPlus",
        "used_for": "Labelled illicit and licit Bitcoin transactions and wallet addresses for model training. Its "
                    "transactions extend the original Elliptic Data Set.",
        "licence": "No licence stated in the repository: research use with citation, data not redistributed or "
                   "committed; confirm permitted use with your supervisor.",
        "citation": "Elmougy, Y. and Liu, L. (2023). Demystifying Fraudulent Transactions and Illicit Nodes in the "
                    "Bitcoin Network for Financial Forensics. KDD '23. Builds on Weber, M. et al. (2019), Anti-Money "
                    "Laundering in Bitcoin (Elliptic Data Set), KDD Workshop on Anomaly Detection in Finance.",
    },
    {
        "name": "Elliptic2 dataset",
        "category": "Research datasets",
        "url": "https://www.kaggle.com/datasets/ellipticco/elliptic2-data-set",
        "used_for": "Labelled Bitcoin money-flow subgraphs for the separate research subgraph models "
                    "(not used to score live lookups).",
        "licence": "CC BY-NC-ND 4.0: non-commercial, attribution, no redistribution of modified data.",
        "citation": "Bellei, C. et al. (2024). The Shape of Money Laundering: Subgraph Representation Learning on the "
                    "Blockchain with the Elliptic2 Dataset. KDD MLF Workshop 2024.",
    },
    {
        "name": "Tranco top sites list",
        "category": "Research datasets",
        "url": "https://tranco-list.eu/",
        "used_for": "Top 100,000 websites, shown as popularity context only (never as proof a site is safe).",
        "licence": "Inputs include CC BY-NC data: internal, non-commercial use only.",
        "citation": "Le Pochat, V. et al. (2019). Tranco: A Research-Oriented Top Sites Ranking Hardened Against "
                    "Manipulation. NDSS 2019.",
    },
    {
        "name": "Neon serverless Postgres",
        "category": "Infrastructure",
        "url": "https://neon.tech/",
        "used_for": "Stores lookups, investigations, cases, user accounts and the audit log (a processor of account "
                    "data; see the privacy policy).",
        "licence": "Managed service (Neon terms of service and data processing addendum).",
        "citation": "",
    },
    {
        "name": "Vercel and Railway hosting",
        "category": "Infrastructure",
        "url": "https://vercel.com/legal/privacy-policy",
        "used_for": "Vercel serves the dashboard pages; Railway runs the backend API. Both see visitors' IP addresses "
                    "in their request logs.",
        "licence": "Managed services (Vercel and Railway terms of service and privacy policies).",
        "citation": "",
    },
    {
        "name": "Google Fonts (Inter, JetBrains Mono)",
        "category": "Infrastructure",
        "url": "https://developers.google.com/fonts/faq/privacy",
        "used_for": "The dashboard's typefaces, loaded from Google's servers, which receive the visitor's IP address.",
        "licence": "Fonts: SIL Open Font License 1.1. Service: Google Fonts terms and privacy policy.",
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
        "name": "Sigma.js and Graphology",
        "category": "Open-source software",
        "url": "https://www.sigmajs.org/",
        "used_for": "Draws the large graphs in the Graph explorer (WebGL) and lays them out (ForceAtlas2).",
        "licence": "MIT",
        "citation": "",
    },
    {
        "name": "FastAPI, React and Vite",
        "category": "Open-source software",
        "url": "https://fastapi.tiangolo.com/",
        "used_for": "The backend API (FastAPI) and the dashboard in the browser (React, built with Vite).",
        "licence": "MIT",
        "citation": "",
    },
    {
        "name": "scikit-learn, XGBoost, SHAP and NetworkX",
        "category": "Open-source software",
        "url": "https://scikit-learn.org/",
        "used_for": "Training and running the risk models (scikit-learn, XGBoost), explaining their scores (SHAP), "
                    "and graph measures (NetworkX).",
        "licence": "scikit-learn BSD-3-Clause; XGBoost Apache-2.0; SHAP MIT; NetworkX BSD-3-Clause.",
        "citation": "Pedregosa, F. et al. (2011), JMLR 12; Chen, T. and Guestrin, C. (2016), KDD '16; Lundberg, S. and "
                    "Lee, S.-I. (2017), NeurIPS.",
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
            _apply_revision(conn)
            return
        conn.execute("INSERT INTO app_meta (key, value) SELECT 'source_added:' || name, now()::text "
                     "FROM unnest(%s::text[]) AS name ON CONFLICT DO NOTHING", (list(ADDED_LATER),))
        conn.execute("INSERT INTO app_meta (key, value) VALUES (%s, now()::text) ON CONFLICT DO NOTHING",
                     (f"sources_revision:{SOURCES_REVISION}",))
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO data_sources (name, category, url, used_for, licence, citation, position)
                   VALUES (%(name)s, %(category)s, %(url)s, %(used_for)s, %(licence)s, %(citation)s, %(position)s)""",
                [{**source, "position": index} for index, source in enumerate(DEFAULT_SOURCES)],
            )


# Defaults added after a database may already have been seeded. Each is added once; if an admin
# deletes it afterwards it stays deleted.
ADDED_LATER = ("Etherscan API V2", "CryptoScamDB", "MetaMask eth-phishing-detect", "ScamSniffer scam database",
               "Polkadot{.js} phishing list", "Phishing.Database", "Tranco top sites list",
               "Alchemy API", "Elliptic2 dataset", "Dune cex.addresses", "Blockscout PRO API",
               "Vercel and Railway hosting", "Google Fonts (Inter, JetBrains Mono)", "Sigma.js and Graphology",
               "scikit-learn, XGBoost, SHAP and NetworkX", "x402analysis", "x402scan facilitator list",
               "Coinbase CDP SQL API")

# Bump this when the licence or usage text of built-in sources changes. Once per revision, rows with a built-in
# name that no admin has edited (updated_by is empty) are refreshed from DEFAULT_SOURCES; edited rows are left alone.
SOURCES_REVISION = "2026-11-14"


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


def _apply_revision(conn) -> None:
    first = conn.execute("INSERT INTO app_meta (key, value) VALUES (%s, now()::text) ON CONFLICT DO NOTHING "
                         "RETURNING key", (f"sources_revision:{SOURCES_REVISION}",)).fetchone()
    if not first:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """UPDATE data_sources SET url = %(url)s, used_for = %(used_for)s, licence = %(licence)s,
                      citation = %(citation)s, category = %(category)s, updated_at = now()
               WHERE name = %(name)s AND updated_by IS NULL""",
            DEFAULT_SOURCES,
        )


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
