"""The audit log: one row for every change that matters (roles, sources, cases, BigQuery)."""
import logging

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

log = logging.getLogger("app.audit")


def record(pool: ConnectionPool, user, action: str, target: str | None = None, detail: dict | None = None) -> None:
    """Write one audit row. A failure is logged, never raised: the action itself already happened."""
    try:
        with pool.connection() as conn:
            conn.execute(
                "INSERT INTO audit_log (user_id, user_email, action, target, detail) VALUES (%s, %s, %s, %s, %s)",
                (getattr(user, "id", None), getattr(user, "email", None), action, target, Jsonb(detail or {})),
            )
    except Exception:
        log.exception("Could not write the audit log entry for %s", action)


def recent(pool: ConnectionPool, limit: int) -> list[dict]:
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, at, user_email, action, target, detail FROM audit_log ORDER BY at DESC LIMIT %s",
            (limit,),
        )
        return cur.fetchall()
