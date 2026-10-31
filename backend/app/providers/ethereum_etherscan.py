"""Ethereum adapter for the Etherscan API V2 (https://api.etherscan.io/v2/api, chain id 1).

Needs ETHERSCAN_API_KEY in backend/.env. The free plan allows 3 calls per second and 100,000
per day, so every call waits for its turn (one per 0.36 s for the whole API process) and answers
are cached. A daily-limit error pauses Etherscan calls for an hour instead of retrying.

The API key travels as a query parameter, so it is kept out of everything we store or show:
provenance URLs are built without it, and httpx's request log lines are redacted (evm_common).
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx

from app.providers.base import ProviderError
from app.providers.evm_common import (
    MAX_RECORDS_PER_CALL,
    TTL_CONFIRMED_TX,
    TTL_RECENT,
    from_rpc,
    from_unix,
    lower,
    plain_text,
    register_secret,
    to_int,
    utcnow,
)
from app.providers.failover import TTLCache, _MISSING
from app.schemas.bitcoin import ProviderHealth, Provenance
from app.schemas.ethereum import (
    EthAddressSummary,
    EthInternalTransfer,
    EthNftTransfer,
    EthTokenTransfer,
    EthTransaction,
)

log = logging.getLogger("app.providers.etherscan")

MIN_SECONDS_BETWEEN_CALLS = 0.36  # free plan: 3 calls per second
DAILY_LIMIT_PAUSE_SECONDS = 3600.0
NETWORK_NAMES = {1: "mainnet", 11155111: "sepolia"}

_utcnow, _int, _time, _text, _addr = utcnow, to_int, from_unix, plain_text, lower


class EtherscanProvider:
    name = "etherscan.io"

    def __init__(self, api_key: str, base_url: str = "https://api.etherscan.io/v2/api", chain_id: int = 1,
                 timeout_seconds: float = 10, transport: httpx.AsyncBaseTransport | None = None,
                 min_interval: float = MIN_SECONDS_BETWEEN_CALLS):
        self._api_key = api_key
        register_secret(api_key)
        self.base_url = base_url
        self.chain_id = chain_id
        self.network = NETWORK_NAMES.get(chain_id, f"chain-{chain_id}")
        self.timeout_seconds = timeout_seconds
        self._transport = transport  # tests replay recorded responses through this
        self._min_interval = min_interval
        self._next_slot = 0.0
        self._paused_until = 0.0
        self._nft_complete: dict[str, bool] = {}
        self.cache = TTLCache(max_entries=1024)
        self.requests = self.answers = self.failures = 0
        self.last_error: str | None = None
        self.last_error_at: datetime | None = None
        self.last_success_at: datetime | None = None

    # ---- HTTP -----------------------------------------------------------------------------

    def public_url(self, params: dict) -> str:
        """The request URL without the API key: safe to save and show."""
        return f"{self.base_url}?{urlencode({'chainid': self.chain_id, **params})}"

    async def _wait_turn(self) -> None:
        now = time.monotonic()
        start = max(now, self._next_slot)
        self._next_slot = start + self._min_interval
        if start > now:
            await asyncio.sleep(start - now)

    async def _call(self, params: dict, allow_empty: bool = False) -> tuple[object, str]:
        """One API call. Returns (result, public URL). Raises ProviderError with a safe message."""
        wait = self._paused_until - time.monotonic()
        if wait > 0:
            raise ProviderError(f"Etherscan's daily limit was reached; lookups resume in {wait / 60:.0f} minutes",
                                429, retry_after=wait)
        url = self.public_url(params)
        for attempt in range(2):
            await self._wait_turn()
            self.requests += 1
            try:
                result = await self._get(params)
            except ProviderError as exc:
                if exc.status_code == 429 and exc.retry_after is None and attempt == 0:
                    await asyncio.sleep(1.1)  # per-second limit: one more try after a pause
                    continue
                self._failed(exc)
                raise
            if result is None and not allow_empty:
                raise ProviderError("Etherscan has no record of that", 404)
            self.answers += 1
            self.last_success_at = _utcnow()
            return result, url
        raise AssertionError("unreachable")

    async def _get(self, params: dict) -> object:
        query = {"chainid": self.chain_id, **params, "apikey": self._api_key}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, transport=self._transport) as client:
                response = await client.get(self.base_url, params=query, headers={"Accept": "application/json"})
        except httpx.TimeoutException:
            raise ProviderError(f"Etherscan did not answer within {self.timeout_seconds} seconds", 504)
        except httpx.HTTPError as exc:
            raise ProviderError(f"Could not reach Etherscan: {exc.__class__.__name__}", 502)
        if response.status_code == 429:
            raise ProviderError("Etherscan rate limit reached", 429)
        if response.status_code >= 400:
            raise ProviderError(f"Etherscan returned HTTP {response.status_code}", 502)
        try:
            body = response.json()
        except ValueError:
            raise ProviderError("Etherscan returned a response that is not JSON", 502)
        if not isinstance(body, dict):
            raise ProviderError("Etherscan returned an unexpected response", 502)

        # Proxy (JSON-RPC) calls: {"jsonrpc", "result"} or {"error": {...}}.
        if "jsonrpc" in body:
            if body.get("error"):
                raise ProviderError(f"Etherscan node error: {str(body['error'].get('message', ''))[:200]}", 502)
            return body.get("result")

        # Account calls: {"status": "1"|"0", "message", "result"}. HTTP 200 can still be an error.
        if str(body.get("status")) == "1":
            return body.get("result")
        message = str(body.get("message", ""))
        detail = body.get("result")
        text = f"{message} {detail if isinstance(detail, str) else ''}".lower()
        if "no transactions found" in text or "no records found" in text or "no token transfers" in text:
            return []
        if "rate limit" in text:
            if "daily" in text or "day" in text:
                raise ProviderError("Etherscan's daily limit was reached", 429, retry_after=DAILY_LIMIT_PAUSE_SECONDS)
            raise ProviderError("Etherscan rate limit reached (3 calls per second)", 429)
        if "api key" in text or "apikey" in text:
            raise ProviderError("Etherscan rejected the API key (check ETHERSCAN_API_KEY in backend/.env)", 502)
        if "invalid" in text:
            raise ProviderError(f"Etherscan rejected the request: {str(detail)[:200]}", 404)
        raise ProviderError(f"Etherscan returned an error: {message} {str(detail)[:200]}".strip(), 502)

    def _failed(self, exc: ProviderError) -> None:
        if exc.status_code == 404:
            return  # a definite "no", not a provider problem
        self.failures += 1
        self.last_error = exc.message
        self.last_error_at = _utcnow()
        if exc.retry_after:
            self._paused_until = time.monotonic() + exc.retry_after

    def _provenance(self, url: str) -> Provenance:
        return Provenance(provider=self.name, source_url=url, retrieved_at=_utcnow(), data_mode="live")

    async def _cached(self, key: tuple, ttl, fetch):
        value = self.cache.get(key)
        if value is not _MISSING:
            return value
        value = await fetch()
        self.cache.put(key, value, ttl(value) if callable(ttl) else ttl)
        return value

    # ---- lookups ------------------------------------------------------------------------

    async def get_address_summary(self, address: str) -> EthAddressSummary:
        address = address.lower()

        async def fetch():
            (balance, url), (nonce, _) = await asyncio.gather(
                self._call({"module": "account", "action": "balance", "address": address, "tag": "latest"}),
                self._call({"module": "proxy", "action": "eth_getTransactionCount", "address": address, "tag": "latest"}))
            return EthAddressSummary(
                entity_id=f"ethereum:{self.network}:address:{address}", network=self.network, chain_id=self.chain_id,
                address=address, balance_wei=str(_int(balance) or 0), sent_tx_count=_int(nonce, 16) or 0,
                provenance=self._provenance(url))

        return await self._cached(("address", address), TTL_RECENT, fetch)

    def list_url(self, action: str, address: str, limit: int = MAX_RECORDS_PER_CALL) -> str:
        """The key-free URL of one list call (for evidence)."""
        return self.public_url(self._list_params(action, address, limit))

    @staticmethod
    def _list_params(action: str, address: str, limit: int) -> dict:
        return {"module": "account", "action": action, "address": address.lower(), "startblock": 0,
                "endblock": 99_999_999, "page": 1, "offset": min(limit, MAX_RECORDS_PER_CALL), "sort": "desc"}

    async def _list(self, action: str, address: str, limit: int, convert) -> list:
        """Newest-first rows of one list call (up to 1,000), converted; cached for 30 s."""
        params = self._list_params(action, address, limit)

        async def fetch():
            rows, url = await self._call(params, allow_empty=True)
            if not isinstance(rows, list):
                raise ProviderError(f"Etherscan returned an unexpected {action} list", 502)
            provenance = self._provenance(url)
            return [convert(row, provenance) for row in rows]

        return list(await self._cached((action, address.lower(), params["offset"]), TTL_RECENT, fetch))

    async def get_address_transactions(self, address: str, limit: int = MAX_RECORDS_PER_CALL) -> list[EthTransaction]:
        """The newest normal transactions sent or received by the address (one call, up to 1,000)."""
        return await self._list("txlist", address, limit, self._from_txlist)

    async def get_token_transfers(self, address: str, limit: int = MAX_RECORDS_PER_CALL) -> list[EthTokenTransfer]:
        """The newest ERC-20 transfers to or from the address (one call, up to 1,000)."""
        return await self._list("tokentx", address, limit, lambda row, _: EthTokenTransfer(
            tx_hash=row["hash"].lower(), block_number=_int(row.get("blockNumber")),
            block_time=_time(_int(row.get("timeStamp"))), token_contract=row["contractAddress"].lower(),
            token_symbol=_text(row.get("tokenSymbol")), token_decimals=_int(row.get("tokenDecimal")),
            from_address=row["from"].lower(), to_address=row["to"].lower(), value_raw=str(_int(row.get("value")) or 0)))

    async def get_internal_transfers(self, address: str, limit: int = MAX_RECORDS_PER_CALL) -> list[EthInternalTransfer]:
        """The newest internal ETH transfers (contract traces) touching the address (one call, up to 1,000)."""
        return await self._list("txlistinternal", address, limit, lambda row, _: EthInternalTransfer(
            tx_hash=row["hash"].lower(), block_number=_int(row.get("blockNumber")),
            block_time=_time(_int(row.get("timeStamp"))), from_address=row["from"].lower(),
            to_address=_addr(row.get("to")) or None, contract_created=_addr(row.get("contractAddress")) or None,
            value_wei=str(_int(row.get("value")) or 0), kind=row.get("type") or None, failed=row.get("isError") == "1"))

    async def get_nft_transfers(self, address: str, limit: int = MAX_RECORDS_PER_CALL) -> list[EthNftTransfer]:
        """The newest ERC-721 and ERC-1155 transfers (two calls, up to 1,000 each), newest first."""
        def nft(standard):
            return lambda row, _: EthNftTransfer(
                tx_hash=row["hash"].lower(), block_number=_int(row.get("blockNumber")),
                block_time=_time(_int(row.get("timeStamp"))), standard=standard,
                token_contract=row["contractAddress"].lower(), token_name=_text(row.get("tokenName")),
                token_symbol=_text(row.get("tokenSymbol")), token_id=str(row.get("tokenID") or ""),
                quantity=str(row.get("tokenValue") or "1"), from_address=row["from"].lower(),
                to_address=row["to"].lower())
        erc721, erc1155 = await asyncio.gather(self._list("tokennfttx", address, limit, nft("ERC-721")),
                                               self._list("token1155tx", address, limit, nft("ERC-1155")))
        self._nft_complete[address.lower()] = len(erc721) < MAX_RECORDS_PER_CALL and len(erc1155) < MAX_RECORDS_PER_CALL
        return sorted(erc721 + erc1155, key=lambda t: t.block_number or 0, reverse=True)

    async def get_tx_internal_transfers(self, tx_hash: str) -> list[EthInternalTransfer]:
        """Internal ETH transfers made by contracts inside one transaction."""
        tx_hash = tx_hash.lower()

        async def fetch():
            rows, _ = await self._call({"module": "account", "action": "txlistinternal", "txhash": tx_hash},
                                       allow_empty=True)
            if not isinstance(rows, list):
                raise ProviderError("Etherscan returned an unexpected internal transfer list", 502)
            return [EthInternalTransfer(
                tx_hash=tx_hash, block_number=_int(row.get("blockNumber")), block_time=_time(_int(row.get("timeStamp"))),
                from_address=row["from"].lower(), to_address=_addr(row.get("to")) or None,
                contract_created=_addr(row.get("contractAddress")) or None, value_wei=str(_int(row.get("value")) or 0),
                kind=row.get("type") or None, failed=row.get("isError") == "1") for row in rows]

        return list(await self._cached(("tx_internal", tx_hash), TTL_CONFIRMED_TX, fetch))

    LIST_ACTIONS = {"get_address_transactions": "txlist", "get_token_transfers": "tokentx",
                    "get_internal_transfers": "txlistinternal", "get_nft_transfers": "tokennfttx"}

    def history_complete(self, method: str, address: str, count: int) -> bool:
        """Each Etherscan list is its own call, so a list is complete when it is under the 1,000 cap
        (the NFT list joins two calls, ERC-721 and ERC-1155, and is complete when neither hit the cap)."""
        if method == "get_nft_transfers":
            return self._nft_complete.get(address.lower(), count < MAX_RECORDS_PER_CALL)
        return count < MAX_RECORDS_PER_CALL

    def describe(self, method: str, address: str) -> tuple[str, str]:
        """(source name, key-free URL of the list call) for evidence."""
        return self.name, self.list_url(self.LIST_ACTIONS[method], address)

    def token_transfers_url(self, address: str, limit: int = MAX_RECORDS_PER_CALL) -> str:
        return self.list_url("tokentx", address, limit)

    def _from_txlist(self, row: dict, provenance: Provenance) -> EthTransaction:
        failed = row.get("isError") == "1" or row.get("txreceipt_status") == "0"
        gas_used, gas_price = _int(row.get("gasUsed")), _int(row.get("gasPrice"))
        name = (row.get("functionName") or "").split("(")[0] or None
        selector = row.get("methodId") if row.get("methodId") not in (None, "", "0x") else None
        tx_hash = row["hash"].lower()
        return EthTransaction(
            entity_id=f"ethereum:{self.network}:tx:{tx_hash}", network=self.network, chain_id=self.chain_id,
            tx_hash=tx_hash, status="failed" if failed else "success", block_number=_int(row.get("blockNumber")),
            block_time=_time(_int(row.get("timeStamp"))), from_address=row["from"].lower(),
            to_address=_addr(row.get("to")) or None, contract_created=_addr(row.get("contractAddress")) or None,
            value_wei=str(_int(row.get("value")) or 0),
            fee_wei=str(gas_used * gas_price) if gas_used is not None and gas_price is not None else None,
            method=name or selector, provenance=provenance,
        )

    async def get_transaction(self, tx_hash: str) -> EthTransaction:
        """One transaction with its receipt (status, fee) and the ERC-20 transfers it emitted."""
        tx_hash = tx_hash.lower()

        async def fetch():
            tx, url = await self._call({"module": "proxy", "action": "eth_getTransactionByHash", "txhash": tx_hash})
            receipt, _ = await self._call({"module": "proxy", "action": "eth_getTransactionReceipt", "txhash": tx_hash},
                                          allow_empty=True)
            block_number = _int(tx.get("blockNumber"), 16)
            block_time = None
            if block_number is not None:
                block, _ = await self._call({"module": "proxy", "action": "eth_getBlockByNumber",
                                             "tag": hex(block_number), "boolean": "false"}, allow_empty=True)
                block_time = _time(_int((block or {}).get("timestamp"), 16))
            return self._from_rpc(tx, receipt, block_number, block_time, url)

        return await self._cached(("tx", tx_hash),
                                  lambda t: TTL_CONFIRMED_TX if t.status != "pending" else TTL_RECENT, fetch)

    def _from_rpc(self, tx: dict, receipt: dict | None, block_number: int | None, block_time: datetime | None,
                  url: str) -> EthTransaction:
        return from_rpc(tx, receipt, block_number, block_time, self._provenance(url), "ethereum", self.network,
                        self.chain_id)

    async def health(self) -> ProviderHealth:
        """Latest block number. Never raises."""
        try:
            tip, _ = await self._call({"module": "proxy", "action": "eth_blockNumber"})
            return ProviderHealth(provider=self.name, reachable=True, tip_height=_int(tip, 16), checked_at=_utcnow(),
                                  detail="ok")
        except ProviderError as exc:
            return ProviderHealth(provider=self.name, reachable=False, tip_height=None, checked_at=_utcnow(),
                                  detail=exc.message)

    async def status(self, check: bool = False) -> dict:
        """Same shape as the Bitcoin failover status, so the Status page can show both."""
        paused = max(0.0, self._paused_until - time.monotonic())
        return {
            "sources": [{
                "provider": self.name, "priority": 1, "base_url": self.base_url, "chain_id": self.chain_id,
                "ready": paused <= 0, "cooldown_seconds_left": round(paused, 1),
                "requests": self.requests, "answers": self.answers, "failures": self.failures,
                "last_error": self.last_error, "last_error_at": self.last_error_at,
                "last_success_at": self.last_success_at,
                "health": (await self.health()).model_dump(mode="json") if check and paused <= 0 else None,
            }],
            "cache_entries": len(self.cache),
            "recent_fallbacks": [],
        }
