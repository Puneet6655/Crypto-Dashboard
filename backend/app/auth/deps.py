"""FastAPI dependencies that decide who may call a route.

    @router.get("/thing", dependencies=[Depends(require_admin)])

Tests replace ``current_user`` with a function that returns a fixed user.
"""
from fastapi import Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.accounts import CurrentUser, user_for_token
from app.auth.settings import auth_settings
from app.db import NullStore
from app.store import get_store
from app.workspace.database import NEEDS_DATABASE

GUEST = CurrentUser(id=None, email="guest@local", display_name="Guest (sign-in turned off)", role="viewer")

_bearer = HTTPBearer(auto_error=False, description="The token returned by /api/auth/login")

SIGN_IN = {"WWW-Authenticate": "Bearer"}


async def optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    store: NullStore = Depends(get_store),
) -> CurrentUser | None:
    """The signed-in user, or None. Never raises for a missing or bad token."""
    if credentials is None or not credentials.credentials:
        return None
    pool = getattr(store, "pool", None)
    if not store.enabled or pool is None:
        return None
    return await run_in_threadpool(user_for_token, pool, credentials.credentials)


async def current_user(
    user: CurrentUser | None = Depends(optional_user),
    store: NullStore = Depends(get_store),
) -> CurrentUser:
    """The signed-in user. 401 when there is none (unless sign-in is turned off)."""
    if user is not None:
        return user
    if not auth_settings().required:
        return GUEST
    if not store.enabled:
        raise HTTPException(status_code=503, detail=f"Sign-in is not available. {NEEDS_DATABASE}")
    raise HTTPException(status_code=401, detail="Please sign in.", headers=SIGN_IN)


def _require(role: str, wording: str):
    async def dependency(user: CurrentUser = Depends(current_user)) -> CurrentUser:
        if user.id is None or not user.has(role):
            raise HTTPException(status_code=403, detail=f"Only {wording} can do this.")
        return user

    dependency.__name__ = f"require_{role}"
    return dependency


require_viewer = current_user
require_analyst = _require("analyst", "analysts and admins")
require_admin = _require("admin", "admins")
