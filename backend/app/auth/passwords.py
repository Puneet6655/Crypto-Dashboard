"""Password hashing with scrypt from Python's standard library (memory-hard, no extra package).

A stored hash looks like ``scrypt$16384$8$5$<salt>$<hash>``. The cost settings travel with each
hash, so they can be raised later: old hashes still verify and are replaced at the next login.
The plain password is never stored or logged.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

# OWASP's minimum scrypt setting (N=2^14, r=8, p=5): about 16 MiB of memory per hash.
COST_N = 2**14
BLOCK_R = 8
PARALLEL_P = 5
KEY_BYTES = 32
_MAX_MEMORY = 64 * 1024 * 1024

MIN_PASSWORD_LENGTH = 10


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int, length: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=length, maxmem=_MAX_MEMORY)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _scrypt(password, salt, COST_N, BLOCK_R, PARALLEL_P, KEY_BYTES)
    return "$".join(["scrypt", str(COST_N), str(BLOCK_R), str(PARALLEL_P), _b64(salt), _b64(digest)])


def verify_password(password: str, stored: str) -> bool:
    """True if the password matches. Compares in constant time; a malformed hash never matches."""
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = _unb64(digest)
        actual = _scrypt(password, _unb64(salt), int(n), int(r), int(p), len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def needs_rehash(stored: str) -> bool:
    """True when the hash was made with older (weaker) settings than the current ones."""
    parts = stored.split("$")
    return len(parts) != 6 or parts[0] != "scrypt" or parts[1:4] != [str(COST_N), str(BLOCK_R), str(PARALLEL_P)]


_dummy_hash: str | None = None


def burn_time(password: str) -> None:
    """Spend the same time as a real check, so a login for an unknown email is not faster
    (which would tell an attacker which emails have accounts)."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password(secrets.token_urlsafe(16))
    verify_password(password, _dummy_hash)


def password_problem(password: str) -> str | None:
    """A sentence saying what is wrong with a new password, or None if it is acceptable."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters."
    if len(password) > 256:
        return "Use at most 256 characters."
    if password.strip() != password:
        return "The password cannot start or end with a space."
    if len(set(password)) < 4:
        return "That password is too repetitive."
    return None
