"""A user's own data: download all of it (right of access) and delete all of it (right to erasure).

What counts as one user's data:
- the account row (email, name, user type, organisation, when terms were accepted) and its sign-in sessions;
- cases they created, with every item in them;
- items they added to other people's cases (with their notes);
- addresses they put on the live-alert watchlist;
- audit log rows about them. On deletion these are kept without the name or email, so the log still shows
  that something happened but no longer who did it.

Lookups and investigations are not linked to a user, so they are not personal data and are not touched.
Database backups (Neon point-in-time restore) roll off on their own within the provider's retention window.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.alerts import manual
from app.auth import accounts

log = logging.getLogger("app.privacy")


def export_user_data(pool: ConnectionPool, user_id: int) -> dict | None:
    """Everything stored about one user, as plain JSON. Password and session token hashes are left out:
    they are secrets, not information about the person, and would only help someone attack the account."""
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT {accounts.USER_COLUMNS} FROM app_users WHERE id = %s", (user_id,))
        profile = cur.fetchone()
        if profile is None:
            return None
        email = profile["email"]
        cur.execute("SELECT created_at, expires_at, user_agent FROM app_sessions WHERE user_id = %s "
                    "ORDER BY created_at DESC", (user_id,))
        sessions = cur.fetchall()
        cur.execute("SELECT id, title, description, status, priority, created_at, updated_at FROM cases "
                    "WHERE created_by = %s ORDER BY created_at", (user_id,))
        cases = cur.fetchall()
        cur.execute("SELECT id, case_id, item_type, value, network, label, note, source, added_at FROM case_items "
                    "WHERE added_by = %s OR case_id IN (SELECT id FROM cases WHERE created_by = %s) ORDER BY added_at",
                    (user_id, user_id))
        items = cur.fetchall()
        cur.execute("SELECT id, at, action, target, detail FROM audit_log WHERE user_id = %s OR target = %s "
                    "ORDER BY at", (user_id, email))
        audit_rows = cur.fetchall()
    own_case_ids = {c["id"] for c in cases}
    for case in cases:
        case["items"] = [i for i in items if i["case_id"] == case["id"]]
    other_items = [i for i in items if i["case_id"] not in own_case_ids]
    manual._ensure(pool)
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT chain, address, label, added_at FROM alert_watch_addresses "
                    "WHERE lower(added_by_email) = %s ORDER BY added_at", (email,))
        watchlist = cur.fetchall()
    return {
        "exported_at": datetime.now(timezone.utc),
        "about": "Everything the Blockchain Intelligence dashboard stores about your account. Your password is "
                 "stored only as a one-way hash and is not included. Your date of birth was checked at sign-up "
                 "and never stored.",
        "profile": profile,
        "sessions": sessions,
        "cases_you_created": cases,
        "items_you_added_to_other_cases": other_items,
        "watchlist_addresses_you_added": watchlist,
        "audit_log": audit_rows,
    }


def delete_user_data(pool: ConnectionPool, user_id: int) -> dict | None:
    """Delete one user and everything that is theirs, in one transaction. Returns what was removed, or None if
    there is no such user. Callers check first that this is not the last admin."""
    manual._ensure(pool)  # the watchlist table is created on first use; make sure it exists
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT email FROM app_users WHERE id = %s FOR UPDATE", (user_id,))
        row = cur.fetchone()
        if row is None:
            return None
        email = row[0]
        cur.execute("""DELETE FROM case_items WHERE added_by = %s
                       AND case_id NOT IN (SELECT id FROM cases WHERE created_by = %s)""", (user_id, user_id))
        items_elsewhere = cur.rowcount
        cur.execute("DELETE FROM cases WHERE created_by = %s", (user_id,))  # their items go with them (cascade)
        cases = cur.rowcount
        cur.execute("DELETE FROM alert_watch_addresses WHERE lower(added_by_email) = %s", (email,))
        watchlist = cur.rowcount
        cur.execute("""UPDATE audit_log SET user_id = NULL, user_email = NULL,
                              target = CASE WHEN target = %s THEN NULL ELSE target END
                       WHERE user_id = %s OR user_email = %s OR target = %s""", (email, user_id, email, email))
        audit_rows = cur.rowcount
        cur.execute("DELETE FROM app_users WHERE id = %s", (user_id,))  # sessions go with it (cascade)
    accounts.forget_user(user_id)
    return {"cases": cases, "items_in_other_cases": items_elsewhere, "watchlist_addresses": watchlist,
            "audit_rows_anonymised": audit_rows}


def purge_old_data(pool: ConnectionPool, audit_days: int, inactive_account_days: int) -> dict:
    """Apply the retention periods in the privacy policy. Safe to run often.

    - expired sign-in sessions are deleted;
    - audit log rows older than audit_days are deleted (0 keeps them);
    - accounts with no sign-in for inactive_account_days are deleted with all their data (0 turns this off).
      Admin accounts are never deleted this way, so the dashboard cannot lose its last admin.
    """
    removed = {"sessions": 0, "audit_rows": 0, "accounts": 0}
    with pool.connection() as conn:
        removed["sessions"] = conn.execute("DELETE FROM app_sessions WHERE expires_at < now()").rowcount
        if audit_days:
            removed["audit_rows"] = conn.execute(
                "DELETE FROM audit_log WHERE at < now() - make_interval(days => %s)", (audit_days,)).rowcount
        stale = []
        if inactive_account_days:
            stale = [r[0] for r in conn.execute(
                """SELECT id FROM app_users WHERE role <> 'admin'
                   AND coalesce(last_login_at, created_at) < now() - make_interval(days => %s)""",
                (inactive_account_days,)).fetchall()]
    for user_id in stale:
        if delete_user_data(pool, user_id) is not None:
            removed["accounts"] += 1
    if any(removed.values()):
        log.info("Retention clean-up removed %s", removed)
    return removed
