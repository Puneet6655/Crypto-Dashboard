"""Admin-only: manage users and read the audit log."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.auth import accounts, privacy
from app.auth.accounts import CurrentUser
from app.auth.deps import require_admin
from app.db import NullStore
from app.store import get_store
from app.workspace import audit
from app.workspace.database import pool_of

router = APIRouter(prefix="/api/admin", tags=["admin"])


class UserChange(BaseModel):
    role: Literal["viewer", "analyst", "admin"] | None = None
    disabled: bool | None = None


@router.get("/users")
def list_users(_: CurrentUser = Depends(require_admin), store: NullStore = Depends(get_store)) -> list[dict]:
    return accounts.list_users(pool_of(store))


@router.patch("/users/{user_id}")
def change_user(user_id: int, body: UserChange, admin: CurrentUser = Depends(require_admin),
                store: NullStore = Depends(get_store)) -> dict:
    pool = pool_of(store)
    target = accounts.get_user(pool, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="No user with that ID.")
    losing_admin = target["role"] == "admin" and not target["disabled"] and (
        (body.role is not None and body.role != "admin") or body.disabled is True)
    if losing_admin and accounts.count_active_admins(pool) <= 1:
        raise HTTPException(status_code=409, detail="This is the last admin. Make someone else an admin first.")
    row = accounts.update_user(pool, user_id, role=body.role, disabled=body.disabled)
    audit.record(pool, admin, "user.changed", target["email"],
                 {k: v for k, v in body.model_dump().items() if v is not None} | {"before_role": target["role"]})
    return row


@router.delete("/users/{user_id}")
def delete_user(user_id: int, admin: CurrentUser = Depends(require_admin), store: NullStore = Depends(get_store)) -> dict:
    """Delete someone's account and all their data, e.g. when they ask by email instead of using the Account page."""
    pool = pool_of(store)
    target = accounts.get_user(pool, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="No user with that ID.")
    if target["id"] == admin.id:
        raise HTTPException(status_code=409, detail="Delete your own account from the Account page.")
    if target["role"] == "admin" and not target["disabled"] and accounts.count_active_admins(pool) <= 1:
        raise HTTPException(status_code=409, detail="This is the last admin. Make someone else an admin first.")
    removed = privacy.delete_user_data(pool, user_id)
    audit.record(pool, admin, "user.deleted_by_admin", None, removed or {})
    return {"deleted": removed is not None, "removed": removed}


@router.get("/audit")
def audit_log(limit: int = Query(100, ge=1, le=500), _: CurrentUser = Depends(require_admin),
              store: NullStore = Depends(get_store)) -> list[dict]:
    return audit.recent(pool_of(store), limit)
