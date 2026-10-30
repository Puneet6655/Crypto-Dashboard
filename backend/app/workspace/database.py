"""Shared helpers for features that cannot work without the database."""
from fastapi import HTTPException
from psycopg_pool import ConnectionPool

from app.db import NullStore

NEEDS_DATABASE = "This needs the database. Set DATABASE_URL in backend/.env and restart the API."


def pool_of(store: NullStore) -> ConnectionPool:
    """The connection pool, or HTTP 503 when the API runs without a database."""
    pool = getattr(store, "pool", None)
    if not store.enabled or pool is None:
        raise HTTPException(status_code=503, detail=NEEDS_DATABASE)
    return pool
