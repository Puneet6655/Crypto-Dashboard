"""Users and sessions in Postgres.

A session token is 32 random bytes handed to the browser once. Only its SHA-256 hash is stored,
so reading the database does not let anyone sign in as someone else.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

ROLES = ("viewer", "analyst", "admin")
ROLE_RANK = {role: rank for rank, role in enumerate(ROLES)}

_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")

# user_type, organisation and the consent columns were added later (schema.sql adds them to old databases).
# The date of birth is checked at sign-up and never stored: age_confirmed_at only records that the check passed.
USER_COLUMNS = ("id, email, display_name, role, disabled, created_at, last_login_at, user_type, organisation, "
                "age_confirmed_at, terms_version, terms_accepted_at")
_USER_COLUMNS = USER_COLUMNS


class EmailTaken(ValueError):
    pass


@dataclass(frozen=True)
class CurrentUser:
    id: int | None
    email: str
    display_name: str
    role: str

    def has(self, role: str) -> bool:
        return ROLE_RANK.get(self.role, -1) >= ROLE_RANK[role]

    def public(self) -> dict:
        return {"id": self.id, "email": self.email, "display_name": self.display_name, "role": self.role}


def normalize_email(email: str) -> str:
    return email.strip().lower()


def email_problem(email: str) -> str | None:
    return None if _EMAIL.match(email) and len(email) <= 254 else "That does not look like an email address."


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---- users ------------------------------------------------------------------------------------

def create_user(pool: ConnectionPool, email: str, display_name: str, password_hash: str, role: str = "viewer",
                *, user_type: str | None = None, organisation: str | None = None,
                terms_version: str | None = None) -> dict:
    """Create an account. Pass user_type and terms_version for sign-ups (the age check has already passed);
    accounts made by an admin on the command line leave them empty and complete them at first sign-in."""
    consented = terms_version is not None
    try:
        with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""INSERT INTO app_users (email, display_name, password_hash, role, user_type, organisation,
                                           age_confirmed_at, terms_version, terms_accepted_at)
                    VALUES (%s, %s, %s, %s, %s, %s, CASE WHEN %s THEN now() END, %s, CASE WHEN %s THEN now() END)
                    RETURNING {_USER_COLUMNS}""",
                (normalize_email(email), display_name.strip(), password_hash, role, user_type, organisation,
                 consented, terms_version, consented),
            )
            return cur.fetchone()
    except UniqueViolation as exc:
        raise EmailTaken(email) from exc


def find_user_by_email(pool: ConnectionPool, email: str) -> dict | None:
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT {_USER_COLUMNS}, password_hash FROM app_users WHERE email = %s", (normalize_email(email),))
        return cur.fetchone()


def get_user(pool: ConnectionPool, user_id: int) -> dict | None:
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT {_USER_COLUMNS} FROM app_users WHERE id = %s", (user_id,))
        return cur.fetchone()


def list_users(pool: ConnectionPool) -> list[dict]:
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT {_USER_COLUMNS} FROM app_users ORDER BY created_at")
        return cur.fetchall()


def count_active_admins(pool: ConnectionPool) -> int:
    with pool.connection() as conn:
        return conn.execute("SELECT count(*) FROM app_users WHERE role = 'admin' AND NOT disabled").fetchone()[0]


def update_user(pool: ConnectionPool, user_id: int, *, role: str | None = None, disabled: bool | None = None,
                password_hash: str | None = None) -> dict | None:
    sets, values = [], []
    for column, value in (("role", role), ("disabled", disabled), ("password_hash", password_hash)):
        if value is not None:
            sets.append(f"{column} = %s")
            values.append(value)
    if not sets:
        return get_user(pool, user_id)
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"UPDATE app_users SET {', '.join(sets)} WHERE id = %s RETURNING {_USER_COLUMNS}", (*values, user_id))
        row = cur.fetchone()
        if disabled or password_hash:
            # Disabling an account or changing its password signs it out everywhere.
            cur.execute("DELETE FROM app_sessions WHERE user_id = %s", (user_id,))
    forget_user(user_id)
    return row


def complete_profile(pool: ConnectionPool, user_id: int, user_type: str, organisation: str | None,
                     terms_version: str) -> dict | None:
    """Record the age check, user type and acceptance of the current terms for an existing account."""
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""UPDATE app_users SET user_type = %s, organisation = %s,
                       age_confirmed_at = coalesce(age_confirmed_at, now()),
                       terms_version = %s, terms_accepted_at = now()
                WHERE id = %s RETURNING {_USER_COLUMNS}""",
            (user_type, organisation, terms_version, user_id),
        )
        return cur.fetchone()


def profile_missing(row: dict, terms_version: str) -> list[str]:
    """What this account still has to give before it can use the dashboard: [] when nothing."""
    missing = []
    if not row.get("age_confirmed_at"):
        missing.append("age")
    if not row.get("user_type"):
        missing.append("user_type")
    if row.get("terms_version") != terms_version:
        missing.append("terms")
    return missing


def mark_login(pool: ConnectionPool, user_id: int, new_password_hash: str | None = None) -> None:
    with pool.connection() as conn:
        if new_password_hash:
            conn.execute("UPDATE app_users SET last_login_at = now(), password_hash = %s WHERE id = %s",
                         (new_password_hash, user_id))
        else:
            conn.execute("UPDATE app_users SET last_login_at = now() WHERE id = %s", (user_id,))


# ---- sessions ---------------------------------------------------------------------------------

def create_session(pool: ConnectionPool, user_id: int, hours: float, user_agent: str | None) -> tuple[str, datetime]:
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=hours)
    with pool.connection() as conn:
        conn.execute("DELETE FROM app_sessions WHERE expires_at < now()")  # tidy up old sessions
        conn.execute(
            "INSERT INTO app_sessions (token_hash, user_id, expires_at, user_agent) VALUES (%s, %s, %s, %s)",
            (token_hash(token), user_id, expires_at, (user_agent or "")[:300]),
        )
    return token, expires_at


def delete_session(pool: ConnectionPool, token: str) -> None:
    hashed = token_hash(token)
    with pool.connection() as conn:
        conn.execute("DELETE FROM app_sessions WHERE token_hash = %s", (hashed,))
    _cache.drop(hashed)


def user_for_token(pool: ConnectionPool, token: str) -> CurrentUser | None:
    """The signed-in user for a token, or None if it is unknown, expired or the account is disabled.

    Answers are remembered for a minute so each request does not wait for the database.
    Signing out, disabling a user and changing a role clear the remembered answer at once.
    """
    hashed = token_hash(token)
    cached = _cache.get(hashed)
    if cached is not None:
        return cached
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT u.id, u.email, u.display_name, u.role
               FROM app_sessions s JOIN app_users u ON u.id = s.user_id
               WHERE s.token_hash = %s AND s.expires_at > now() AND NOT u.disabled""",
            (hashed,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    user = CurrentUser(**row)
    _cache.put(hashed, user)
    return user


def forget_user(user_id: int) -> None:
    _cache.drop_user(user_id)


class _SessionCache:
    def __init__(self, ttl_seconds: float = 60.0, max_entries: int = 1000):
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._items: dict[str, tuple[float, CurrentUser]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> CurrentUser | None:
        with self._lock:
            item = self._items.get(key)
            if item is None or item[0] <= time.monotonic():
                self._items.pop(key, None)
                return None
            return item[1]

    def put(self, key: str, user: CurrentUser) -> None:
        with self._lock:
            if len(self._items) >= self.max_entries:
                self._items.clear()
            self._items[key] = (time.monotonic() + self.ttl, user)

    def drop(self, key: str) -> None:
        with self._lock:
            self._items.pop(key, None)

    def drop_user(self, user_id: int) -> None:
        with self._lock:
            for key in [k for k, (_, u) in self._items.items() if u.id == user_id]:
                del self._items[key]


_cache = _SessionCache()
