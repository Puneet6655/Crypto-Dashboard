"""Sign up, sign in, sign out, and who am I.

Sign-up always creates a viewer. Roles are changed by an admin (Users page or the command line).
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth import accounts, passwords, throttle
from app.auth.accounts import CurrentUser
from app.auth.deps import _bearer, current_user
from app.auth.settings import auth_settings
from app.db import NullStore
from app.store import get_store
from app.workspace import audit
from app.workspace.database import pool_of

router = APIRouter(prefix="/api/auth", tags=["auth"])

WRONG_LOGIN = "Wrong email or password."


class SignupRequest(BaseModel):
    email: str = Field(max_length=254)
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(max_length=256)


class LoginRequest(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _session_response(pool, user_row: dict, request: Request) -> dict:
    token, expires_at = accounts.create_session(
        pool, user_row["id"], auth_settings().session_hours, request.headers.get("user-agent"))
    user = CurrentUser(id=user_row["id"], email=user_row["email"], display_name=user_row["display_name"],
                       role=user_row["role"])
    return {"token": token, "expires_at": expires_at, "user": user.public()}


@router.get("/config")
def auth_config(store: NullStore = Depends(get_store)) -> dict:
    """What the login page needs to know before anyone signs in."""
    settings = auth_settings()
    return {
        "auth_required": settings.required,
        "signup_enabled": settings.signup_enabled and store.enabled,
        "database": store.enabled,
        "min_password_length": passwords.MIN_PASSWORD_LENGTH,
    }


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
    try:
        row = accounts.create_user(pool, email, body.display_name, passwords.hash_password(body.password), "viewer")
    except accounts.EmailTaken:
        raise HTTPException(status_code=409, detail="An account with this email already exists. Sign in instead.")
    throttle.signup_by_ip.failed(ip)  # counts sign-ups, not failures
    audit.record(pool, CurrentUser(row["id"], row["email"], row["display_name"], row["role"]), "user.signup", email)
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
    return _session_response(pool, row, request)


@router.post("/logout", status_code=204)
def logout(credentials=Depends(_bearer), store: NullStore = Depends(get_store)) -> None:
    if credentials and store.enabled:
        accounts.delete_session(pool_of(store), credentials.credentials)


@router.get("/me")
def me(user: CurrentUser = Depends(current_user)) -> dict:
    return user.public()


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
