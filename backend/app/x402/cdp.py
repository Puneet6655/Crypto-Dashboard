"""Coinbase CDP SQL API: every x402 payment on Base, not just a watchlist. Optional.

Switched on by CDP_API_KEY_ID and CDP_API_KEY_SECRET in backend/.env (a free key from https://portal.cdp.coinbase.com,
Settings > API Keys > Secret API keys). It needs the PyJWT and cryptography packages. This is the approach x402scan
uses (MIT, https://github.com/Merit-Systems/x402scan): USDC Transfer events on Base whose transaction was sent by a
known facilitator. Free tier limits (Nov 2026): about 2 queries a second, 50,000 rows a query, 30 s timeout.

The bearer-token code follows x402analysis's gatherlib/cdp_auth.py (MIT, Peter Robinson), itself written from
https://docs.cdp.coinbase.com/api-reference/v2/authentication.
"""
from __future__ import annotations

import base64
import os
import secrets
import time
from datetime import datetime, timezone

import httpx

from app.x402.rules import Payment
from app.x402.snapshot import USDC_BASE, norm

HOST = "api.cdp.coinbase.com"
PATH = "/platform/v2/data/query/run"
URL = f"https://{HOST}{PATH}"
DOCS = "https://docs.cdp.coinbase.com/data/sql-api/welcome"


class CdpError(Exception):
    pass


def credentials() -> tuple[str, str] | None:
    key_id = (os.getenv("CDP_API_KEY_ID") or os.getenv("CDP_API_KEY_NAME") or "").strip()
    secret = (os.getenv("CDP_API_KEY_SECRET") or "").strip().replace("\\n", "\n")
    return (key_id, secret) if key_id and secret else None


def missing_packages() -> list[str]:
    missing = []
    try:
        import jwt  # noqa: F401
    except ImportError:
        missing.append("PyJWT")
    try:
        import cryptography  # noqa: F401
    except ImportError:
        missing.append("cryptography")
    return missing


def bearer_token(key_id: str, key_secret: str, method: str = "POST", host: str = HOST, path: str = PATH) -> str:
    import jwt
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    secret = key_secret.strip()
    if secret.startswith("-----BEGIN"):
        key, algorithm = secret, "ES256"
    else:
        try:
            raw = base64.b64decode(secret, validate=True)
        except (ValueError, TypeError) as exc:
            raise CdpError("CDP_API_KEY_SECRET is neither a PEM EC key nor base64 (Ed25519).") from exc
        if len(raw) not in (32, 64):
            raise CdpError(f"CDP_API_KEY_SECRET decodes to {len(raw)} bytes; expected an Ed25519 key (32 or 64).")
        key, algorithm = Ed25519PrivateKey.from_private_bytes(raw[:32]), "EdDSA"
    now = int(time.time())
    claims = {"sub": key_id, "iss": "cdp", "aud": ["cdp_service"], "nbf": now, "exp": now + 120,
              "uri": f"{method.upper()} {host}{path}"}
    return jwt.encode(claims, key, algorithm=algorithm, headers={"kid": key_id, "nonce": secrets.token_hex(16)})


def payments_sql(facilitators: list[str], since: datetime, limit: int = 1000) -> str:
    addresses = ", ".join(f"'{norm(a)}'" for a in facilitators if a.startswith("0x") and all(c in "0123456789abcdefx" for c in norm(a)))
    stamp = since.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return (
        "SELECT transaction_hash, transaction_from, block_number, block_timestamp, "
        "parameters['from'] AS payer, parameters['to'] AS pay_to, parameters['value'] AS value "
        "FROM base.events "
        "WHERE event_signature = 'Transfer(address,address,uint256)' "
        f"AND address = '{USDC_BASE}' "
        f"AND block_timestamp > toDateTime('{stamp}') "
        f"AND transaction_from IN ({addresses}) "
        f"ORDER BY block_timestamp ASC LIMIT {int(limit)}"
    )


class CdpSqlClient:
    def __init__(self, key_id: str, key_secret: str, transport: httpx.AsyncBaseTransport | None = None,
                 timeout: float = 40.0):
        self.key_id, self.key_secret = key_id, key_secret
        self.transport, self.timeout = transport, timeout
        self.queries = 0
        self.rows = 0

    @classmethod
    def from_env(cls) -> "CdpSqlClient | None":
        creds = credentials()
        if creds is None or missing_packages():
            return None
        return cls(*creds)

    async def run(self, sql: str) -> list[dict]:
        token = bearer_token(self.key_id, self.key_secret)
        self.queries += 1
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            r = await client.post(URL, json={"sql": sql}, headers={"Authorization": f"Bearer {token}",
                                                                   "Content-Type": "application/json"})
        if r.status_code >= 400:
            try:
                detail = r.json().get("errorMessage") or r.json().get("message") or r.text[:200]
            except ValueError:
                detail = r.text[:200]
            raise CdpError(f"CDP SQL API HTTP {r.status_code}: {detail}")
        rows = r.json().get("result") or []
        self.rows += len(rows)
        return rows

    async def x402_payments(self, facilitators: list[str], since: datetime, limit: int = 1000) -> list[Payment]:
        rows = await self.run(payments_sql(facilitators, since, limit))
        out = []
        for row in rows:
            try:
                amount = int(row.get("value")) / 1e6
            except (TypeError, ValueError):
                amount = None
            at = row.get("block_timestamp")
            out.append(Payment(chain="base", tx=str(row.get("transaction_hash") or "").lower(),
                               payer=norm(str(row.get("payer") or "")), pay_to=norm(str(row.get("pay_to") or "")),
                               amount=amount, facilitator=norm(str(row.get("transaction_from") or "")),
                               at=str(at).replace(" ", "T") + ("" if "+" in str(at) or "Z" in str(at) else "+00:00") if at else None,
                               block=int(row["block_number"]) if row.get("block_number") is not None else None,
                               method="transferWithAuthorization", gasless=True))
        return out
