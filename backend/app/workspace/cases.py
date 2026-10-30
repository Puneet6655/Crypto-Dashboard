"""Cases: named groups of addresses and transactions, with who added what and when."""
from __future__ import annotations

import re

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

ITEM_TYPES = ("address", "transaction", "domain", "payment", "other")
_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")

_CASE_COLUMNS = """c.id, c.title, c.description, c.status, c.priority, c.created_at, c.updated_at,
                   u.display_name AS created_by_name"""


def clean_value(item_type: str, value: str) -> str:
    """Trim spaces; transaction IDs are compared in lower case (addresses keep their case)."""
    value = value.strip()
    if item_type == "transaction" and _HEX64.match(value):
        return value.lower()
    return value


def value_problem(item_type: str, value: str) -> str | None:
    if not value:
        return "Enter an address, transaction ID or other identifier."
    if len(value) > 200:
        return "That is too long to be an identifier (200 characters at most)."
    if item_type in ("address", "transaction") and re.search(r"\s", value):
        return "Addresses and transaction IDs cannot contain spaces."
    if item_type == "transaction" and not _HEX64.match(value):
        return "A Bitcoin transaction ID is 64 hexadecimal characters."
    return None


def list_cases(pool: ConnectionPool) -> list[dict]:
    """Every case, newest activity first, each with its items (type and value only)."""
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"""SELECT {_CASE_COLUMNS} FROM cases c LEFT JOIN app_users u ON u.id = c.created_by
                        ORDER BY (c.status = 'open') DESC, c.updated_at DESC""")
        cases = cur.fetchall()
        cur.execute("SELECT id, case_id, item_type, value, label FROM case_items ORDER BY added_at")
        items = cur.fetchall()
    by_case: dict[int, list[dict]] = {c["id"]: [] for c in cases}
    for item in items:
        by_case.get(item.pop("case_id"), []).append(item)
    for case in cases:
        case["items"] = by_case[case["id"]]
        case["item_count"] = len(case["items"])
    return cases


def get_case(pool: ConnectionPool, case_id: int) -> dict | None:
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT {_CASE_COLUMNS} FROM cases c LEFT JOIN app_users u ON u.id = c.created_by WHERE c.id = %s",
                    (case_id,))
        case = cur.fetchone()
        if case is None:
            return None
        cur.execute(
            """SELECT i.id, i.item_type, i.value, i.network, i.label, i.note, i.source, i.added_at,
                      u.display_name AS added_by_name
               FROM case_items i LEFT JOIN app_users u ON u.id = i.added_by
               WHERE i.case_id = %s ORDER BY i.added_at""",
            (case_id,),
        )
        case["items"] = cur.fetchall()
        case["item_count"] = len(case["items"])
    return case


def create_case(pool: ConnectionPool, user_id: int, title: str, description: str, priority: str) -> int:
    with pool.connection() as conn:
        return conn.execute(
            "INSERT INTO cases (title, description, priority, created_by) VALUES (%s, %s, %s, %s) RETURNING id",
            (title.strip(), description.strip(), priority, user_id),
        ).fetchone()[0]


def update_case(pool: ConnectionPool, case_id: int, changes: dict) -> bool:
    allowed = {k: v for k, v in changes.items() if k in ("title", "description", "status", "priority") and v is not None}
    if not allowed:
        return True
    sets = ", ".join(f"{column} = %s" for column in allowed)
    with pool.connection() as conn:
        cur = conn.execute(f"UPDATE cases SET {sets}, updated_at = now() WHERE id = %s",
                           (*[v.strip() if isinstance(v, str) else v for v in allowed.values()], case_id))
        return cur.rowcount > 0


def delete_case(pool: ConnectionPool, case_id: int) -> dict | None:
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("DELETE FROM cases WHERE id = %s RETURNING id, title", (case_id,))
        return cur.fetchone()


def add_item(pool: ConnectionPool, case_id: int, user_id: int, item: dict) -> tuple[dict, bool]:
    """Add one item. Returns (item, created); adding the same item twice keeps the first one."""
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """INSERT INTO case_items (case_id, item_type, value, network, label, note, source, added_by)
               VALUES (%(case_id)s, %(item_type)s, %(value)s, %(network)s, %(label)s, %(note)s, %(source)s, %(user_id)s)
               ON CONFLICT (case_id, item_type, value) DO NOTHING
               RETURNING id, item_type, value, network, label, note, source, added_at""",
            {**item, "case_id": case_id, "user_id": user_id},
        )
        row = cur.fetchone()
        created = row is not None
        if not created:
            cur.execute(
                """SELECT id, item_type, value, network, label, note, source, added_at FROM case_items
                   WHERE case_id = %s AND item_type = %s AND value = %s""",
                (case_id, item["item_type"], item["value"]),
            )
            row = cur.fetchone()
        else:
            cur.execute("UPDATE cases SET updated_at = now() WHERE id = %s", (case_id,))
    return row, created


def update_item_note(pool: ConnectionPool, case_id: int, item_id: int, note: str) -> bool:
    with pool.connection() as conn:
        cur = conn.execute("UPDATE case_items SET note = %s WHERE id = %s AND case_id = %s",
                           (note.strip(), item_id, case_id))
        return cur.rowcount > 0


def remove_item(pool: ConnectionPool, case_id: int, item_id: int) -> dict | None:
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("DELETE FROM case_items WHERE id = %s AND case_id = %s RETURNING item_type, value",
                    (item_id, case_id))
        row = cur.fetchone()
        if row:
            cur.execute("UPDATE cases SET updated_at = now() WHERE id = %s", (case_id,))
        return row


def case_exists(pool: ConnectionPool, case_id: int) -> bool:
    with pool.connection() as conn:
        return conn.execute("SELECT 1 FROM cases WHERE id = %s", (case_id,)).fetchone() is not None
