"""Sign-in settings, read from environment variables (backend/.env is loaded by app.config)."""
import os
from dataclasses import dataclass

import app.config  # noqa: F401  (loads backend/.env before the variables below are read)


def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() not in ("0", "false", "no", "off")


@dataclass(frozen=True)
class AuthSettings:
    # When true (the default), every data route needs a signed-in user. Set AUTH_REQUIRED=false
    # only for local work without a database; admin actions still need a real admin session.
    required: bool
    # Whether the Sign up form creates accounts. New accounts are always viewers.
    signup_enabled: bool
    session_hours: float


def auth_settings() -> AuthSettings:
    return AuthSettings(
        required=_flag("AUTH_REQUIRED", "true"),
        signup_enabled=_flag("SIGNUP_ENABLED", "true"),
        session_hours=float(os.getenv("SESSION_HOURS", "168")),
    )
