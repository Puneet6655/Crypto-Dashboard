"""Sign up, sign in, sign out, and who am I. Also your own data: complete your profile, download it, delete it.

Sign-up always creates a viewer. Roles are changed by an admin (Users page or the command line).
Sign-up asks for a date of birth (checked against legal.MIN_AGE and never stored), what kind of user you are,
and agreement to the terms of use and privacy policy (legal.TERMS_VERSION).
"""
import json
import logging
import threading
import time
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app import legal
from app.auth import accounts, passwords, privacy, throttle
from app.auth.accounts import CurrentUser
from app.auth.deps import _bearer, current_user
from app.auth.settings import auth_settings
from app.db import NullStore
from app.store import get_store
from app.workspace import audit
from app.workspace.database import pool_of

router = APIRouter(prefix="/api/auth", tags=["auth"])

WRONG_LOGIN = "Wrong email or password."


log = logging.getLogger("app.auth")

DELETE_WORD = "DELETE"


class SignupRequest(BaseModel):
    email: str = Field(max_length=254)
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(max_length=256)
    date_of_birth: date | None = None
    user_type: str | None = Field(None, max_length=40)
    organisation: str | None = Field(None, max_length=120)
    accept_terms: bool = False


class ProfileRequest(BaseModel):
    """For accounts made before these questions existed, or when the terms change."""
    date_of_birth: date | None = None
    user_type: str | None = Field(None, max_length=40)
    organisation: str | None = Field(None, max_length=120)
    accept_terms: bool = False


class DeleteAccountRequest(BaseModel):
    password: str = Field(max_length=256)
    confirm: str = Field(max_length=20)


class LoginRequest(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _consent_problem(birth: date | None, user_type: str | None, accepted: bool) -> str | None:
    return (legal.age_problem(birth) or legal.user_type_problem(user_type)
            or (None if accepted else "Please read and accept the terms of use and privacy policy."))


def _profile(row: dict) -> dict:
    """The account details the browser shows, and what is still missing before the dashboard opens."""
    return {
        "user_type": row.get("user_type"),
        "organisation": row.get("organisation"),
        "terms_version": row.get("terms_version"),
        "terms_accepted_at": row.get("terms_accepted_at"),
        "created_at": row.get("created_at"),
        "needs": accounts.profile_missing(row, legal.TERMS_VERSION),
    }


def _public_user(row: dict) -> dict:
    user = CurrentUser(id=row["id"], email=row["email"], display_name=row["display_name"], role=row["role"])
    return {**user.public(), **_profile(row)}


def _session_response(pool, user_row: dict, request: Request) -> dict:
    token, expires_at = accounts.create_session(
        pool, user_row["id"], auth_settings().session_hours, request.headers.get("user-agent"))
    return {"token": token, "expires_at": expires_at, "user": _public_user(user_row)}


# Retention clean-up (privacy.purge_old_data) runs in the background after a sign-in, at most every 12 hours,
# so the policy's retention periods hold without a separate scheduled job.
_PURGE_EVERY = 12 * 3600
_last_purge = [0.0]
_purge_lock = threading.Lock()


def _maybe_purge(pool) -> None:
    with _purge_lock:
        if time.monotonic() - _last_purge[0] < _PURGE_EVERY and _last_purge[0]:
            return
        _last_purge[0] = time.monotonic()
    settings = legal.legal_settings()

    def run() -> None:
        try:
            privacy.purge_old_data(pool, settings.audit_retention_days, settings.inactive_account_days)
        except Exception:
            log.exception("Retention clean-up failed")

    threading.Thread(target=run, name="retention-clean-up", daemon=True).start()


@router.get("/config")
def auth_config(store: NullStore = Depends(get_store)) -> dict:
    """What the login page needs to know before anyone signs in."""
    settings = auth_settings()
    return {
        "auth_required": settings.required,
        "signup_enabled": settings.signup_enabled and store.enabled,
        "database": store.enabled,
        "min_password_length": passwords.MIN_PASSWORD_LENGTH,
        "min_age": legal.MIN_AGE,
        "user_types": legal.USER_TYPES,
        "organisation_types": list(legal.ORGANISATION_TYPES),
        "terms_version": legal.TERMS_VERSION,
    }


@router.get("/legal")
def legal_info() -> dict:
    """Public: who runs the dashboard, how to contact them, and the retention periods (for the legal pages)."""
    return legal.legal_settings().public()


@router.post("/signup", status_code=201)
def signup(body: SignupRequest, request: Request, store: NullStore = Depends(get_store)) -> dict:
    pool = pool_of(store)
    if not auth_settings().signup_enabled:
        raise HTTPException(status_code=403, detail="Sign-up is closed. Ask an admin for an account.")
    ip = _client_ip(request)
    wait = throttle.signup_by_ip.seconds_locked(ip)
    if wait:
        raise HTTPException(status_code=429, detail=f"Too many new accounts from here. Try again in {wait / 60:.0f} minutes.",
                            headers={"Retry-After": str(int(wait) + 1)})
    email = accounts.normalize_email(body.email)
    problem = accounts.email_problem(email) or passwords.password_problem(body.password)
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    if not body.display_name.strip():
        raise HTTPException(status_code=422, detail="Please enter your name.")
    problem = _consent_problem(body.date_of_birth, body.user_type, body.accept_terms)
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    try:
        row = accounts.create_user(pool, email, body.display_name, passwords.hash_password(body.password), "viewer",
                                   user_type=body.user_type,
                                   organisation=legal.clean_organisation(body.user_type, body.organisation),
                                   terms_version=legal.TERMS_VERSION)
    except accounts.EmailTaken:
        raise HTTPException(status_code=409, detail="An account with this email already exists. Sign in instead.")
    throttle.signup_by_ip.failed(ip)  # counts sign-ups, not failures
    audit.record(pool, CurrentUser(row["id"], row["email"], row["display_name"], row["role"]), "user.signup", email,
                 {"user_type": body.user_type, "terms_version": legal.TERMS_VERSION})
    return _session_response(pool, row, request)


@router.post("/login")
def login(body: LoginRequest, request: Request, store: NullStore = Depends(get_store)) -> dict:
    pool = pool_of(store)
    email = accounts.normalize_email(body.email)
    ip = _client_ip(request)
    wait = max(throttle.login_by_email.seconds_locked(email), throttle.login_by_ip.seconds_locked(ip))
    if wait:
        raise HTTPException(status_code=429, detail=f"Too many wrong passwords. Try again in {max(1, round(wait / 60))} minutes.",
                            headers={"Retry-After": str(int(wait) + 1)})
    row = accounts.find_user_by_email(pool, email)
    if row is None:
        passwords.burn_time(body.password)
        ok = False
    else:
        ok = passwords.verify_password(body.password, row["password_hash"])
    if not ok:
        throttle.login_by_email.failed(email)
        throttle.login_by_ip.failed(ip)
        raise HTTPException(status_code=401, detail=WRONG_LOGIN)
    if row["disabled"]:
        raise HTTPException(status_code=403, detail="This account has been turned off. Ask an admin.")
    throttle.login_by_email.succeeded(email)
    new_hash = passwords.hash_password(body.password) if passwords.needs_rehash(row["password_hash"]) else None
    accounts.mark_login(pool, row["id"], new_hash)
    _maybe_purge(pool)
    return _session_response(pool, row, request)


@router.post("/logout", status_code=204)
def logout(credentials=Depends(_bearer), store: NullStore = Depends(get_store)) -> None:
    if credentials and store.enabled:
        accounts.delete_session(pool_of(store), credentials.credentials)


@router.get("/me")
def me(user: CurrentUser = Depends(current_user), store: NullStore = Depends(get_store)) -> dict:
    if user.id is None or not store.enabled:
        return {**user.public(), "needs": []}
    row = accounts.get_user(pool_of(store), user.id)
    return _public_user(row) if row else {**user.public(), "needs": []}


@router.post("/profile")
def complete_profile(body: ProfileRequest, user: CurrentUser = Depends(current_user),
                     store: NullStore = Depends(get_store)) -> dict:
    """Give your age, user type and agreement to the current terms (asked once, and again when terms change)."""
    pool = pool_of(store)
    if user.id is None:
        raise HTTPException(status_code=403, detail="Sign in first.")
    row = accounts.get_user(pool, user.id)
    if row is None:
        raise HTTPException(status_code=404, detail="This account no longer exists.")
    # The age is asked only once; later visits (new terms) skip it.
    birth_problem = None if row.get("age_confirmed_at") else legal.age_problem(body.date_of_birth)
    problem = birth_problem or legal.user_type_problem(body.user_type) or (
        None if body.accept_terms else "Please read and accept the terms of use and privacy policy.")
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    row = accounts.complete_profile(pool, user.id, body.user_type,
                                    legal.clean_organisation(body.user_type, body.organisation), legal.TERMS_VERSION)
    audit.record(pool, user, "user.terms_accepted", user.email,
                 {"user_type": body.user_type, "terms_version": legal.TERMS_VERSION})
    return _public_user(row)


@router.get("/export")
def export_my_data(user: CurrentUser = Depends(current_user), store: NullStore = Depends(get_store)) -> Response:
    """Download everything stored about your account as a JSON file (right of access / data portability)."""
    pool = pool_of(store)
    if user.id is None:
        raise HTTPException(status_code=403, detail="Sign in first.")
    data = privacy.export_user_data(pool, user.id)
    if data is None:
        raise HTTPException(status_code=404, detail="This account no longer exists.")
    audit.record(pool, user, "user.data_exported", user.email)
    body = json.dumps(jsonable_encoder(data), indent=2, ensure_ascii=False)
    return Response(content=body, media_type="application/json", headers={
        "Content-Disposition": f'attachment; filename="my-data-{date.today().isoformat()}.json"',
        "Cache-Control": "no-store",
    })


@router.post("/delete-account")
def delete_my_account(body: DeleteAccountRequest, user: CurrentUser = Depends(current_user),
                      store: NullStore = Depends(get_store)) -> dict:
    """Delete your account and everything that is yours (cases, items you added, watchlist entries).
    Needs your password and the word DELETE. Cannot be undone."""
    pool = pool_of(store)
    if user.id is None:
        raise HTTPException(status_code=403, detail="Sign in first.")
    if body.confirm.strip().upper() != DELETE_WORD:
        raise HTTPException(status_code=422, detail=f'Type {DELETE_WORD} to confirm.')
    row = accounts.find_user_by_email(pool, user.email)
    if row is None or not passwords.verify_password(body.password, row["password_hash"]):
        # 403, not 401: a 401 would make the browser sign out before the user can try again.
        raise HTTPException(status_code=403, detail="Your password is not right.")
    if row["role"] == "admin" and not row["disabled"] and accounts.count_active_admins(pool) <= 1:
        raise HTTPException(status_code=409, detail="You are the only admin. Make someone else an admin first, "
                                                    "so the dashboard is not left without one.")
    removed = privacy.delete_user_data(pool, user.id)
    if removed is None:
        raise HTTPException(status_code=404, detail="This account no longer exists.")
    # Recorded without the name or email: the log shows an account was deleted, not whose.
    audit.record(pool, None, "user.deleted", None, removed)
    return {"deleted": True, "removed": removed}


@router.post("/password", status_code=204)
def change_password(body: PasswordChange, user: CurrentUser = Depends(current_user),
                    store: NullStore = Depends(get_store)) -> None:
    """Change your own password. Signs you out everywhere; the browser signs in again."""
    pool = pool_of(store)
    if user.id is None:
        raise HTTPException(status_code=403, detail="Sign in first.")
    row = accounts.find_user_by_email(pool, user.email)
    if row is None or not passwords.verify_password(body.current_password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Your current password is not right.")
    problem = passwords.password_problem(body.new_password)
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    accounts.update_user(pool, user.id, password_hash=passwords.hash_password(body.new_password))
    audit.record(pool, user, "user.password_changed", user.email)
