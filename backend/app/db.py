"""Saves live data to Postgres (Neon) and reads it back.

The store is optional. With no DATABASE_URL, or if the database cannot be
reached, NullStore is used: lookups still work, they just are not saved.

Writes run after the response has been sent (FastAPI background tasks), so a
slow or failing database never slows down or breaks a lookup.
"""
import logging
from pathlib import Path

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.schemas.bitcoin import AddressSummary, ProviderHealth, TransactionSummary
from app.schemas.investigation import InvestigationResult

log = logging.getLogger("app.db")

SCHEMA_FILE = Path(__file__).with_name("schema.sql")


class NullStore:
    """Used when no database is configured. Saves nothing, finds nothing."""

    enabled = False

    def save_address(self, summary: AddressSummary) -> None: ...
    def save_transactions(self, transactions: list[TransactionSummary]) -> None: ...
    def save_investigation(self, result: InvestigationResult) -> None: ...
    def save_health(self, health: ProviderHealth) -> None: ...

    def list_investigations(self, limit: int) -> list[dict]:
        return []

    def get_investigation(self, investigation_id: str) -> dict | None:
        return None

    def address_history(self, address: str, limit: int) -> list[dict]:
        return []

    def close(self) -> None: ...


class PostgresStore(NullStore):
    enabled = True

    def __init__(self, database_url: str):
        # prepare_threshold=None: Neon's pooled endpoint (PgBouncer) does not keep
        # prepared statements between transactions.
        self.pool = ConnectionPool(
            database_url,
            min_size=0,
            max_size=5,
            open=False,
            kwargs={"prepare_threshold": None, "connect_timeout": 15},
        )
        self.pool.open()
        self.create_tables()

    def create_tables(self) -> None:
        """Create any missing tables. Safe to run on every start."""
        with self.pool.connection() as conn:
            conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))

    def close(self) -> None:
        self.pool.close()

    # ---- writes -------------------------------------------------------------

    def save_address(self, summary: AddressSummary) -> None:
        c, u, p = summary.confirmed, summary.unconfirmed, summary.provenance
        with self.pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO address_snapshots (
                    entity_id, network, address,
                    confirmed_tx_count, confirmed_funded_sats, confirmed_spent_sats, confirmed_balance_sats,
                    unconfirmed_tx_count, unconfirmed_funded_sats, unconfirmed_spent_sats, unconfirmed_balance_sats,
                    provider, source_url, retrieved_at, data_mode)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (summary.entity_id, summary.network, summary.address,
                 c.tx_count, c.funded_sats, c.spent_sats, c.balance_sats,
                 u.tx_count, u.funded_sats, u.spent_sats, u.balance_sats,
                 p.provider, p.source_url, p.retrieved_at, p.data_mode),
            )

    def save_transactions(self, transactions: list[TransactionSummary]) -> None:
        """Insert new transactions, or refresh ones already saved (e.g. unconfirmed -> confirmed)."""
        if not transactions:
            return
        tx_rows, flow_rows = [], []
        for tx in transactions:
            p = tx.provenance
            tx_rows.append((
                tx.txid, tx.entity_id, tx.network, tx.status, tx.block_height, tx.block_time, tx.fee_sats,
                len(tx.inputs), len(tx.outputs), sum(o.value_sats or 0 for o in tx.outputs),
                Jsonb([i.model_dump() for i in tx.inputs]), Jsonb([o.model_dump() for o in tx.outputs]),
                p.provider, p.source_url, p.retrieved_at, p.retrieved_at, p.data_mode,
            ))
            for direction, items in (("input", tx.inputs), ("output", tx.outputs)):
                for index, item in enumerate(items):
                    if item.address:
                        flow_rows.append((tx.txid, direction, index, item.address, item.value_sats))

        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO transactions (
                    txid, entity_id, network, status, block_height, block_time, fee_sats,
                    input_count, output_count, total_output_sats, inputs, outputs,
                    provider, source_url, first_seen_at, last_retrieved_at, data_mode)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (txid) DO UPDATE SET
                    status = EXCLUDED.status,
                    block_height = EXCLUDED.block_height,
                    block_time = EXCLUDED.block_time,
                    fee_sats = EXCLUDED.fee_sats,
                    source_url = EXCLUDED.source_url,
                    last_retrieved_at = EXCLUDED.last_retrieved_at
                """,
                tx_rows,
            )
            if flow_rows:
                cur.executemany(
                    """
                    INSERT INTO transaction_flows (txid, direction, position, address, value_sats)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    flow_rows,
                )

    def save_investigation(self, result: InvestigationResult) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO investigations (
                    investigation_id, created_at, mode, method, input_type, input_value, data_mode,
                    finding_count, node_count, edge_count, graph_truncated, result)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (investigation_id) DO NOTHING
                """,
                (result.investigation_id, result.created_at, result.mode, result.method,
                 result.input.type, result.input.value, result.data_mode,
                 len(result.findings), len(result.graph.nodes), len(result.graph.edges), result.graph.truncated,
                 Jsonb(result.model_dump(mode="json"))),
            )

    def save_health(self, health: ProviderHealth) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO provider_health_checks (provider, reachable, tip_height, checked_at, detail) VALUES (%s, %s, %s, %s, %s)",
                (health.provider, health.reachable, health.tip_height, health.checked_at, health.detail),
            )

    # ---- reads --------------------------------------------------------------

    def list_investigations(self, limit: int) -> list[dict]:
        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT investigation_id, created_at, mode, method, input_type, input_value, data_mode,
                       finding_count, node_count, edge_count, graph_truncated
                FROM investigations ORDER BY created_at DESC LIMIT %s
                """,
                (limit,),
            )
            return cur.fetchall()

    def get_investigation(self, investigation_id: str) -> dict | None:
        with self.pool.connection() as conn:
            row = conn.execute("SELECT result FROM investigations WHERE investigation_id = %s", (investigation_id,)).fetchone()
            return row[0] if row else None

    def address_history(self, address: str, limit: int) -> list[dict]:
        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT retrieved_at, confirmed_tx_count, confirmed_balance_sats,
                       unconfirmed_tx_count, unconfirmed_balance_sats, provider
                FROM address_snapshots WHERE address = %s ORDER BY retrieved_at DESC LIMIT %s
                """,
                (address, limit),
            )
            return cur.fetchall()


def open_store(database_url: str | None) -> NullStore:
    """Connect to the database, or return NullStore if it is not configured or not reachable."""
    if not database_url:
        log.warning("DATABASE_URL is not set: live data will not be saved.")
        return NullStore()
    try:
        store = PostgresStore(database_url)
        log.info("Connected to Postgres: live data will be saved.")
        return store
    except Exception as exc:  # never stop the API from starting because of the database
        log.error("Could not connect to Postgres (%s): live data will not be saved.", exc.__class__.__name__)
        return NullStore()


def run_safely(action, *args) -> None:
    """Run one save in a background task. Logs failures instead of raising them."""
    try:
        action(*args)
    except Exception:
        log.exception("Saving to the database failed")
