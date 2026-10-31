"""Alchemy adapter for Solana mainnet (JSON-RPC). Needs ALCHEMY_API_KEY and ALCHEMY_ENABLED=true, and Solana
enabled for the app in Alchemy's dashboard.

Costs on the free plan (30M compute units a month, 300 a second): getBalance and getAccountInfo 10,
getTokenAccountsByOwner 10, getSignaturesForAddress 40 (up to 1,000 signatures), getTransaction 40.
An address investigation reads the signature list and the newest transactions in full, so it costs
roughly 40 + 40 per transaction. Calls are spaced by cost to stay under 250 CU a second.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import httpx

from app.providers.base import ProviderError
from app.providers.evm_common import register_secret, utcnow
from app.providers.failover import _MISSING, TTLCache
from app.schemas.bitcoin import ProviderHealth, Provenance
from app.schemas.solana import (
    SolAddressSummary,
    SolHoldings,
    SolSignature,
    SolTokenHolding,
    SolTransaction,
    SolTransfer,
)

CU_PER_SECOND = 250.0
CU_COST = {"getBalance": 10, "getAccountInfo": 10, "getTokenAccountsByOwner": 10, "getSignaturesForAddress": 40,
           "getTransaction": 40, "getSlot": 20}
MAX_SIGNATURES = 1000  # getSignaturesForAddress limit per call
SYSTEM_PROGRAM = "11111111111111111111111111111111"
TOKEN_PROGRAMS = ("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")
TTL_RECENT = 30.0
TTL_FINAL = 86_400.0
LAMPORTS_PER_SOL = 10**9


def _time(value) -> datetime | None:
    return datetime.fromtimestamp(value, tz=timezone.utc) if isinstance(value, (int, float)) else None


def _decimal_amount(raw: int, decimals: int | None) -> str | None:
    if decimals is None or decimals < 0 or decimals > 30:
        return None
    whole, frac = divmod(raw, 10**decimals)
    frac_text = f"{frac:0{decimals}d}".rstrip("0")[:9] if decimals else ""
    return f"{whole}.{frac_text}" if frac_text else str(whole)


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def parse_transaction(sig: str, data: dict, provenance: Provenance, network: str = "mainnet") -> SolTransaction:
    """A jsonParsed getTransaction result -> SolTransaction (SOL and SPL token transfers, inner instructions too)."""
    meta = data.get("meta") or {}
    message = (data.get("transaction") or {}).get("message") or {}
    keys = []
    for k in message.get("accountKeys") or []:
        keys.append(k if isinstance(k, dict) else {"pubkey": k, "signer": False})
    pubkeys = [k.get("pubkey") for k in keys]

    # Token account -> (owner wallet, mint, decimals), from the token balances before and after.
    token_accounts: dict[str, tuple[str | None, str | None, int | None]] = {}
    for balance in (meta.get("preTokenBalances") or []) + (meta.get("postTokenBalances") or []):
        index = balance.get("accountIndex")
        if isinstance(index, int) and index < len(pubkeys):
            amount = balance.get("uiTokenAmount") or {}
            token_accounts[pubkeys[index]] = (balance.get("owner"), balance.get("mint"), amount.get("decimals"))

    instructions = list(message.get("instructions") or [])
    for inner in meta.get("innerInstructions") or []:
        instructions += inner.get("instructions") or []

    # Token accounts created and closed inside the transaction (e.g. temporary wrapped SOL) have no token
    # balances, so fill their owner, mint and decimals from the instructions that set them up or use them.
    mint_decimals = {mint: dec for _, mint, dec in token_accounts.values() if mint and dec is not None}
    learned: dict[str, dict] = {}
    for ins in instructions:
        parsed = ins.get("parsed")
        if not isinstance(parsed, dict):
            continue
        kind, info = parsed.get("type"), parsed.get("info") or {}
        program = ins.get("program")
        if program in ("spl-token", "spl-token-2022") and kind in ("initializeAccount", "initializeAccount2",
                                                                  "initializeAccount3"):
            learned.setdefault(info.get("account"), {}).update(owner=info.get("owner"), mint=info.get("mint"))
        elif program == "spl-associated-token-account" and kind in ("create", "createIdempotent"):
            learned.setdefault(info.get("account"), {}).update(owner=info.get("wallet"), mint=info.get("mint"))
        elif program in ("spl-token", "spl-token-2022") and kind == "transferChecked":
            dec = (info.get("tokenAmount") or {}).get("decimals")
            if info.get("mint") and dec is not None:
                mint_decimals.setdefault(info["mint"], dec)
                for acct in (info.get("source"), info.get("destination")):
                    learned.setdefault(acct, {}).setdefault("mint", info["mint"])
    for acct, found in learned.items():
        if not acct:
            continue
        owner, mint, dec = token_accounts.get(acct, (None, None, None))
        mint = mint or found.get("mint")
        token_accounts[acct] = (owner or found.get("owner"), mint, dec if dec is not None else mint_decimals.get(mint))

    transfers, programs = [], []
    for ins in instructions:
        program = ins.get("program") or ins.get("programId")
        if program and program not in programs:
            programs.append(program)
        parsed = ins.get("parsed")
        if not isinstance(parsed, dict):
            continue
        kind, info = parsed.get("type"), parsed.get("info") or {}
        if ins.get("program") == "system" and kind in ("transfer", "transferWithSeed", "createAccount",
                                                        "createAccountWithSeed"):
            to = info.get("destination") or info.get("newAccount")
            lamports = _int(info.get("lamports"))
            if info.get("source") and to and lamports:
                transfers.append(SolTransfer(kind="sol", from_address=info["source"], to_address=to,
                                             amount_raw=str(lamports)))
        elif ins.get("program") in ("spl-token", "spl-token-2022") and kind in ("transfer", "transferChecked"):
            source, dest = info.get("source"), info.get("destination")
            if not source or not dest:
                continue
            src_owner, src_mint, src_dec = token_accounts.get(source, (None, None, None))
            dst_owner, dst_mint, dst_dec = token_accounts.get(dest, (None, None, None))
            token_amount = info.get("tokenAmount") or {}
            amount = _int(token_amount.get("amount") or info.get("amount"))
            mint = info.get("mint") or src_mint or dst_mint
            decimals = token_amount.get("decimals", src_dec if src_dec is not None else dst_dec)
            if decimals is None:
                decimals = mint_decimals.get(mint)
            transfers.append(SolTransfer(
                kind="token", from_address=src_owner or info.get("authority") or source,
                to_address=dst_owner or dest, amount_raw=str(amount), mint=mint, decimals=decimals))

    pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
    changes = {}
    for i, key in enumerate(pubkeys):
        if i < len(pre) and i < len(post) and key and post[i] != pre[i]:
            changes[key] = post[i] - pre[i]
    return SolTransaction(
        entity_id=f"solana:{network}:tx:{sig}", network=network, signature=sig,
        status="failed" if meta.get("err") is not None else "success", slot=data.get("slot"),
        block_time=_time(data.get("blockTime")), fee_lamports=meta.get("fee"),
        signers=[k["pubkey"] for k in keys if k.get("signer") and k.get("pubkey")],
        programs=programs, transfers=transfers, balance_changes=changes, provenance=provenance)


class SolanaProvider:
    name = "alchemy.com"
    chain = "solana"
    network = "mainnet"

    def __init__(self, api_key: str, timeout_seconds: float = 10, transport: httpx.AsyncBaseTransport | None = None,
                 cu_per_second: float = CU_PER_SECOND):
        self._url = f"https://solana-mainnet.g.alchemy.com/v2/{api_key}"
        self.base_url = "https://solana-mainnet.g.alchemy.com/v2/"  # key-free, safe to show
        register_secret(api_key)
        self.timeout_seconds = timeout_seconds
        self._transport = transport
        self._cu_per_second = cu_per_second
        self._next_slot = 0.0
        self.cache = TTLCache(max_entries=2048)
        self.requests = self.answers = self.failures = 0
        self.last_error: str | None = None

    async def _wait_turn(self, method: str) -> None:
        cost = CU_COST.get(method, 40)
        now = time.monotonic()
        start = max(now, self._next_slot)
        self._next_slot = start + cost / self._cu_per_second
        if start > now:
            await asyncio.sleep(start - now)

    async def _rpc(self, method: str, params: list) -> object:
        await self._wait_turn(method)
        self.requests += 1
        try:
            result = await self._post(method, params)
        except ProviderError as exc:
            if exc.status_code != 404:
                self.failures += 1
                self.last_error = exc.message
            raise
        self.answers += 1
        return result

    async def _post(self, method: str, params: list) -> object:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, transport=self._transport) as client:
                response = await client.post(self._url, json=payload, headers={"Accept": "application/json"})
        except httpx.TimeoutException:
            raise ProviderError(f"Alchemy (Solana) did not answer within {self.timeout_seconds} seconds", 504)
        except httpx.HTTPError as exc:
            raise ProviderError(f"Could not reach Alchemy (Solana): {exc.__class__.__name__}", 502)
        if response.status_code == 429:
            raise ProviderError("Alchemy rate limit reached", 429, retry_after=5)
        if response.status_code in (401, 403):
            raise ProviderError(f"Alchemy refused the request (HTTP {response.status_code}): check ALCHEMY_API_KEY "
                                "and that Solana is enabled for the app in the Alchemy dashboard", 502)
        if response.status_code >= 400:
            raise ProviderError(f"Alchemy returned HTTP {response.status_code}", 502)
        try:
            body = response.json()
        except ValueError:
            raise ProviderError("Alchemy returned a response that is not JSON", 502)
        error = body.get("error") if isinstance(body, dict) else None
        if error:
            message = str(error.get("message", ""))[:200]
            if error.get("code") == 429 or "rate limit" in message.lower() or "capacity" in message.lower():
                raise ProviderError(f"Alchemy limit reached: {message}", 429, retry_after=5)
            if "invalid" in message.lower() or error.get("code") in (-32602, -32600):
                raise ProviderError(f"Alchemy rejected the request: {message}", 404)
            raise ProviderError(f"Alchemy error: {message}", 502)
        if not isinstance(body, dict) or "result" not in body:
            raise ProviderError("Alchemy returned an unexpected response", 502)
        return body["result"]

    def _provenance(self, detail: str) -> Provenance:
        return Provenance(provider=self.name, source_url=f"{self.base_url} {detail}", retrieved_at=utcnow(),
                          data_mode="live")

    async def _cached(self, key: tuple, ttl, fetch):
        value = self.cache.get(key)
        if value is not _MISSING:
            return value
        value = await fetch()
        self.cache.put(key, value, ttl(value) if callable(ttl) else ttl)
        return value

    # ---- address ----------------------------------------------------------------------------

    async def get_address_summary(self, address: str) -> SolAddressSummary:
        async def fetch():
            balance, info = await asyncio.gather(
                self._rpc("getBalance", [address, {"commitment": "confirmed"}]),
                self._rpc("getAccountInfo", [address, {"encoding": "base64", "dataSlice": {"offset": 0, "length": 0},
                                                       "commitment": "confirmed"}]))
            value = (info or {}).get("value") if isinstance(info, dict) else None
            return SolAddressSummary(
                entity_id=f"solana:{self.network}:address:{address}", network=self.network, address=address,
                balance_lamports=str(_int((balance or {}).get("value") if isinstance(balance, dict) else balance)),
                exists=value is not None, owner_program=(value or {}).get("owner"),
                executable=bool((value or {}).get("executable")),
                provenance=self._provenance(f"getBalance {address}"))

        return await self._cached(("address", address), TTL_RECENT, fetch)

    async def get_signatures(self, address: str, limit: int = MAX_SIGNATURES) -> list[SolSignature]:
        """The newest transactions the address took part in, newest first (one call, up to 1,000)."""
        async def fetch():
            rows = await self._rpc("getSignaturesForAddress",
                                   [address, {"limit": min(limit, MAX_SIGNATURES), "commitment": "confirmed"}])
            return [SolSignature(signature=r["signature"], slot=_int(r.get("slot")), block_time=_time(r.get("blockTime")),
                                 failed=r.get("err") is not None) for r in rows or [] if r.get("signature")]

        return await self._cached(("signatures", address, limit), TTL_RECENT, fetch)

    async def get_holdings(self, address: str) -> SolHoldings:
        """SOL balance and SPL token accounts with a balance (classic and Token-2022 programs, 30 CU)."""
        async def fetch():
            summary, *accounts = await asyncio.gather(
                self.get_address_summary(address),
                *(self._rpc("getTokenAccountsByOwner", [address, {"programId": p}, {"encoding": "jsonParsed"}])
                  for p in TOKEN_PROGRAMS))
            tokens = []
            for result in accounts:
                for acc in (result or {}).get("value") or []:
                    info = ((((acc.get("account") or {}).get("data") or {}).get("parsed") or {}).get("info")) or {}
                    amount = info.get("tokenAmount") or {}
                    raw = _int(amount.get("amount"))
                    if raw > 0 and info.get("mint"):
                        decimals = amount.get("decimals")
                        tokens.append(SolTokenHolding(mint=info["mint"], token_account=acc.get("pubkey", ""),
                                                      amount_raw=str(raw), amount=_decimal_amount(raw, decimals),
                                                      decimals=decimals))
            return SolHoldings(address=address, balance_lamports=summary.balance_lamports, tokens=tokens,
                               provenance=self._provenance(f"getTokenAccountsByOwner {address}"))

        return await self._cached(("holdings", address), 300.0, fetch)

    # ---- transactions -------------------------------------------------------------------------

    async def get_transaction(self, signature: str) -> SolTransaction:
        async def fetch():
            data = await self._rpc("getTransaction", [signature, {"encoding": "jsonParsed", "commitment": "confirmed",
                                                                  "maxSupportedTransactionVersion": 0}])
            if not data:
                raise ProviderError("Alchemy has no record of that Solana transaction (it may be too new or "
                                    "not confirmed yet)", 404)
            return parse_transaction(signature, data, self._provenance(f"getTransaction {signature}"), self.network)

        return await self._cached(("tx", signature), TTL_FINAL, fetch)

    async def get_transactions(self, signatures: list[str]) -> list[SolTransaction | None]:
        """Several transactions; one that cannot be read is None instead of failing the whole list."""
        async def one(sig):
            try:
                return await self.get_transaction(sig)
            except ProviderError as exc:
                if exc.status_code == 429:
                    raise
                return None

        return list(await asyncio.gather(*(one(s) for s in signatures)))

    async def health(self) -> ProviderHealth:
        try:
            slot = await self._rpc("getSlot", [])
            return ProviderHealth(provider=self.name, reachable=True, tip_height=_int(slot), checked_at=utcnow(),
                                  detail="ok (latest slot)")
        except ProviderError as exc:
            return ProviderHealth(provider=self.name, reachable=False, tip_height=None, checked_at=utcnow(),
                                  detail=exc.message)
