"""Alchemy adapter for Ethereum and Base (JSON-RPC plus alchemy_getAssetTransfers).

Needs ALCHEMY_API_KEY (or ALCHEMY_ENDPOINT, which contains the key) in backend/.env.

Free plan (alchemy.com/pricing, checked 2026-10-31): 30M compute units (CU) a month and 300 CU
per second. alchemy_getAssetTransfers costs 120 CU, so calls are spaced by their CU cost to stay
under 250 CU per second for the whole API process. Alchemy's terms ask for no attribution badge;
they do forbid scraping and spreading use over several accounts to get round limits.

Address history comes from two groups of transfer calls, each a pair (sent by / received by the
address) of up to 1,000 records: ETH (normal and internal transfers) and tokens (ERC-20, ERC-721,
ERC-1155). Keeping them apart stops token spam from filling the 1,000-record page and pushing out
older ETH transactions. Each pair serves both lists in its group, so all four lists cost 480 CU.
Asset transfers carry no gas fee, success flag or called function, so transactions from Alchemy
lists have status "unknown" and no fee; a single-transaction lookup reads the receipt and has both.

The key is part of the URL, so it is kept out of provenance (key-free URLs) and log lines.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime

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
from app.providers.failover import _MISSING, TTLCache
from app.schemas.bitcoin import ProviderHealth, Provenance
from app.usage import meter
from app.schemas.ethereum import (
    EthAddressSummary,
    EthHoldings,
    EthNftHolding,
    EthNftHoldings,
    EthTokenHolding,
    EthInternalTransfer,
    EthNftTransfer,
    EthTokenTransfer,
    EthTransaction,
)

CU_PER_SECOND = 250.0  # free plan allows 300; keep a margin
CU_COST = {"alchemy_getAssetTransfers": 120, "eth_blockNumber": 10, "alchemy_getTokenBalances": 20,
           "alchemy_getTokenMetadata": 10, "prices": 40, "getNFTsForOwner": 480}
MAX_TOKENS = 40  # tokens shown; metadata costs 10 CU each
MAX_PRICED = 100  # tokens whose price is asked for (4 calls of 25), so real tokens are not cut off by spam
PRICES_BATCH = 25  # Prices API limit per request
TTL_HOLDINGS = 300.0
DEFAULT_CU = 26
# Transfer categories fetched together. A list method only triggers the calls for its group.
GROUPS = {"eth": ["external", "internal"], "tokens": ["erc20", "erc721", "erc1155"]}
METHOD_GROUP = {"get_address_transactions": "eth", "get_internal_transfers": "eth",
                "get_token_transfers": "tokens", "get_nft_transfers": "tokens"}
NETWORKS = {  # chain name -> (Alchemy network slug, chain id)
    "ethereum": ("eth-mainnet", 1),
    "base": ("base-mainnet", 8453),
}


def alchemy_url(slug: str, api_key: str) -> str:
    return f"https://{slug}.g.alchemy.com/v2/{api_key}"


def _float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _usd(prices: list | None) -> float | None:
    for price in prices or []:
        if str(price.get("currency", "")).lower() == "usd":
            return _float(price.get("value"))
    return None


def _decimal_amount(raw: int, decimals: int | None) -> str | None:
    if decimals is None or decimals < 0 or decimals > 77:
        return None
    whole, frac = divmod(raw, 10 ** decimals)
    frac_text = f"{frac:0{decimals}d}".rstrip("0")[:6] if decimals else ""
    return f"{whole}.{frac_text}" if frac_text else str(whole)


def _iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class AlchemyProvider:
    name = "alchemy.com"
    unsupported = frozenset({"get_tx_internal_transfers"})  # needs the paid trace API

    def __init__(self, api_key: str, chain: str = "ethereum", timeout_seconds: float = 10,
                 transport: httpx.AsyncBaseTransport | None = None, cu_per_second: float = CU_PER_SECOND,
                 url: str | None = None):
        slug, self.chain_id = NETWORKS[chain]
        self.slug = slug
        self._api_key = api_key
        self.chain = chain
        self.network = "mainnet"
        self._url = url or alchemy_url(slug, api_key)
        self.base_url = f"https://{slug}.g.alchemy.com/v2/"  # key-free, safe to show
        register_secret(api_key)
        self.timeout_seconds = timeout_seconds
        self._transport = transport
        self._cu_per_second = cu_per_second
        self._next_slot = 0.0
        self.cache = TTLCache(max_entries=1024)
        self._inflight: dict[tuple[str, str], asyncio.Future] = {}
        self._complete: dict[tuple[str, str], bool] = {}  # (address, group) -> did both calls reach the oldest record?
        self.requests = self.answers = self.failures = 0
        self.last_error: str | None = None

    # ---- JSON-RPC ---------------------------------------------------------------------------

    async def _wait_turn(self, method: str) -> None:
        cost = CU_COST.get(method, DEFAULT_CU)
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
            meter.record("alchemy", f"{self.chain} {method}", CU_COST.get(method, DEFAULT_CU), ok=exc.status_code == 404)
            if exc.status_code != 404:
                self.failures += 1
                self.last_error = exc.message
            raise
        meter.record("alchemy", f"{self.chain} {method}", CU_COST.get(method, DEFAULT_CU))
        self.answers += 1
        return result

    async def _post(self, method: str, params: list) -> object:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        body = await self._send("POST", self._url, json=payload)
        error = body.get("error") if isinstance(body, dict) else None
        if error:
            message = str(error.get("message", ""))[:200]
            if error.get("code") == 429 or "capacity" in message.lower() or "rate limit" in message.lower():
                raise ProviderError(f"Alchemy limit reached: {message}", 429)
            if "invalid" in message.lower():
                raise ProviderError(f"Alchemy rejected the request: {message}", 404)
            raise ProviderError(f"Alchemy error: {message}", 502)
        if not isinstance(body, dict) or "result" not in body:
            raise ProviderError("Alchemy returned an unexpected response", 502)
        return body["result"]

    async def _rest(self, cost_name: str, http_method: str, url: str, **kwargs) -> dict:
        """A call to one of Alchemy's REST APIs (NFT, Prices), paced like the JSON-RPC calls."""
        await self._wait_turn(cost_name)
        self.requests += 1
        try:
            body = await self._send(http_method, url, **kwargs)
        except ProviderError as exc:
            meter.record("alchemy", f"{self.chain} {cost_name}", CU_COST.get(cost_name, DEFAULT_CU), ok=False)
            self.failures += 1
            self.last_error = exc.message
            raise
        meter.record("alchemy", f"{self.chain} {cost_name}", CU_COST.get(cost_name, DEFAULT_CU))
        if not isinstance(body, dict):
            raise ProviderError("Alchemy returned an unexpected response", 502)
        self.answers += 1
        return body

    async def _send(self, http_method: str, url: str, **kwargs) -> object:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, transport=self._transport) as client:
                response = await client.request(http_method, url, headers={"Accept": "application/json"}, **kwargs)
        except httpx.TimeoutException:
            raise ProviderError(f"Alchemy did not answer within {self.timeout_seconds} seconds", 504)
        except httpx.HTTPError as exc:
            raise ProviderError(f"Could not reach Alchemy: {exc.__class__.__name__}", 502)
        if response.status_code == 429:
            raise ProviderError("Alchemy rate limit reached", 429)
        if response.status_code in (401, 403):
            raise ProviderError(f"Alchemy refused the request (HTTP {response.status_code}): check ALCHEMY_API_KEY "
                                f"and that {self.chain} is enabled for the app in the Alchemy dashboard", 502)
        if response.status_code >= 400:
            raise ProviderError(f"Alchemy returned HTTP {response.status_code}", 502)
        try:
            return response.json()
        except ValueError:
            raise ProviderError("Alchemy returned a response that is not JSON", 502)

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

    def history_complete(self, method: str, address: str, count: int) -> bool:
        """True when the list holds the address's whole history of that kind. Each pair of transfer calls
        is shared by the two lists in its group (ETH: normal + internal; tokens: ERC-20 + NFTs)."""
        return self._complete.get((address.lower(), METHOD_GROUP.get(method, "eth")), False)

    def describe(self, method: str, address: str) -> tuple[str, str]:
        """(source name, key-free description of the request) for evidence."""
        return self.name, f"{self.base_url} alchemy_getAssetTransfers {"/".join(GROUPS[METHOD_GROUP.get(method, "eth")])} fromAddress/toAddress={address.lower()}"

    # ---- address ----------------------------------------------------------------------------

    async def get_address_summary(self, address: str) -> EthAddressSummary:
        address = address.lower()

        async def fetch():
            balance, nonce = await asyncio.gather(self._rpc("eth_getBalance", [address, "latest"]),
                                                  self._rpc("eth_getTransactionCount", [address, "latest"]))
            return EthAddressSummary(
                entity_id=f"{self.chain}:{self.network}:address:{address}", network=self.network,
                chain_id=self.chain_id, address=address, balance_wei=str(to_int(balance, 16) or 0),
                sent_tx_count=to_int(nonce, 16) or 0, provenance=self._provenance(f"eth_getBalance {address}"))

        return await self._cached(("address", address), TTL_RECENT, fetch)

    async def _transfers(self, address: str, group: str) -> list[dict]:
        """Every transfer in the group sent by or to the address (two calls, up to 1,000 each), deduplicated,
        newest first. Concurrent callers share one fetch."""
        address = address.lower()
        key = (address, group)
        cached = self.cache.get(("transfers", *key))
        if cached is not _MISSING:
            return cached
        if key in self._inflight:
            # shield: a cancelled waiter must not cancel the fetch other callers share
            return await asyncio.shield(self._inflight[key])
        future = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        try:
            base = {"fromBlock": "0x0", "toBlock": "latest", "category": GROUPS[group], "order": "desc",
                    "withMetadata": True, "excludeZeroValue": False, "maxCount": hex(MAX_RECORDS_PER_CALL)}
            sent, received = await asyncio.gather(
                self._rpc("alchemy_getAssetTransfers", [{**base, "fromAddress": address}]),
                self._rpc("alchemy_getAssetTransfers", [{**base, "toAddress": address}]))
            seen, rows = set(), []
            for row in (sent or {}).get("transfers", []) + (received or {}).get("transfers", []):
                row_key = row.get("uniqueId") or (row.get("hash"), row.get("category"), row.get("from"), row.get("to"))
                if row_key not in seen:
                    seen.add(row_key)
                    rows.append(row)
            rows.sort(key=lambda r: to_int(r.get("blockNum"), 16) or 0, reverse=True)
            # A call that returned a pageKey stopped at 1,000 records: older history of this group is missing.
            self._complete[key] = not ((sent or {}).get("pageKey") or (received or {}).get("pageKey"))
            self.cache.put(("transfers", *key), rows, TTL_RECENT)
            if not future.done():
                future.set_result(rows)
            return rows
        except BaseException as exc:
            if not future.done():
                if isinstance(exc, asyncio.CancelledError):
                    future.cancel()
                else:
                    future.set_exception(exc)
                    future.exception()  # mark retrieved, so an unawaited future does not warn
            raise
        finally:
            self._inflight.pop(key, None)

    async def recent_transfers(self, address: str, max_count: int = 20) -> list[dict]:
        """The newest transfers sent by or to the address (ETH, internal ETH and ERC-20), newest first.
        Two alchemy_getAssetTransfers calls (240 CU) and no cache, so live alerts see new transfers at once."""
        address = address.lower()
        base = {"fromBlock": "0x0", "toBlock": "latest", "category": ["external", "internal", "erc20"],
                "order": "desc", "withMetadata": False, "excludeZeroValue": True, "maxCount": hex(max_count)}
        sent, received = await asyncio.gather(
            self._rpc("alchemy_getAssetTransfers", [{**base, "fromAddress": address}]),
            self._rpc("alchemy_getAssetTransfers", [{**base, "toAddress": address}]))
        rows = (sent or {}).get("transfers", []) + (received or {}).get("transfers", [])
        rows.sort(key=lambda r: to_int(r.get("blockNum"), 16) or 0, reverse=True)
        return rows

    def _common(self, row: dict) -> dict:
        return {"tx_hash": row["hash"].lower(), "block_number": to_int(row.get("blockNum"), 16),
                "block_time": _iso((row.get("metadata") or {}).get("blockTimestamp"))}

    def _raw_value(self, row: dict) -> int:
        return to_int((row.get("rawContract") or {}).get("value"), 16) or 0

    async def get_address_transactions(self, address: str, limit: int = MAX_RECORDS_PER_CALL) -> list[EthTransaction]:
        rows = [r for r in await self._transfers(address, "eth") if r.get("category") == "external"]
        provenance = self._provenance(f"alchemy_getAssetTransfers external {address.lower()}")
        return [EthTransaction(
            entity_id=f"{self.chain}:{self.network}:tx:{r['hash'].lower()}", network=self.network,
            chain_id=self.chain_id, status="unknown", from_address=r["from"].lower(), to_address=lower(r.get("to")),
            contract_created=None, value_wei=str(self._raw_value(r)), fee_wei=None, method=None,
            provenance=provenance, **self._common(r)) for r in rows][:limit]

    async def get_token_transfers(self, address: str, limit: int = MAX_RECORDS_PER_CALL) -> list[EthTokenTransfer]:
        rows = [r for r in await self._transfers(address, "tokens") if r.get("category") == "erc20"]
        return [EthTokenTransfer(
            token_contract=((r.get("rawContract") or {}).get("address") or "").lower(), token_symbol=plain_text(r.get("asset")),
            token_decimals=to_int((r.get("rawContract") or {}).get("decimal"), 16), from_address=r["from"].lower(),
            to_address=(r.get("to") or "").lower(), value_raw=str(self._raw_value(r)), **self._common(r))
            for r in rows][:limit]

    async def get_internal_transfers(self, address: str, limit: int = MAX_RECORDS_PER_CALL) -> list[EthInternalTransfer]:
        rows = [r for r in await self._transfers(address, "eth") if r.get("category") == "internal"]
        return [EthInternalTransfer(
            from_address=r["from"].lower(), to_address=lower(r.get("to")), contract_created=None,
            value_wei=str(self._raw_value(r)), kind="call", failed=False, **self._common(r)) for r in rows][:limit]

    async def get_nft_transfers(self, address: str, limit: int = MAX_RECORDS_PER_CALL) -> list[EthNftTransfer]:
        out = []
        for r in await self._transfers(address, "tokens"):
            contract = ((r.get("rawContract") or {}).get("address") or "").lower()
            shared = dict(token_contract=contract, token_name=None, token_symbol=plain_text(r.get("asset")),
                          from_address=r["from"].lower(), to_address=(r.get("to") or "").lower(), **self._common(r))
            if r.get("category") == "erc721":
                out.append(EthNftTransfer(standard="ERC-721", token_id=str(to_int(r.get("tokenId"), 16) or 0),
                                          quantity="1", **shared))
            elif r.get("category") == "erc1155":
                for item in r.get("erc1155Metadata") or []:
                    out.append(EthNftTransfer(standard="ERC-1155", token_id=str(to_int(item.get("tokenId"), 16) or 0),
                                              quantity=str(to_int(item.get("value"), 16) or 0), **shared))
        return out[:2 * limit]

    # ---- holdings now (Token, Prices and NFT APIs) -----------------------------------------------

    async def _prices_by_address(self, contracts: list[str]) -> dict[str, float]:
        url = f"https://api.g.alchemy.com/prices/v1/{self._api_key}/tokens/by-address"
        out: dict[str, float] = {}
        for start in range(0, len(contracts), PRICES_BATCH):
            chunk = contracts[start:start + PRICES_BATCH]
            body = await self._rest("prices", "POST", url, json={
                "addresses": [{"network": self.slug, "address": c} for c in chunk]})
            for item in body.get("data") or []:
                price = _usd(item.get("prices"))
                if price is not None and item.get("address"):
                    out[item["address"].lower()] = price
        return out

    async def _eth_price(self) -> float | None:
        url = f"https://api.g.alchemy.com/prices/v1/{self._api_key}/tokens/by-symbol"
        body = await self._rest("prices", "GET", url, params={"symbols": "ETH"})
        for item in body.get("data") or []:
            if str(item.get("symbol", "")).upper() == "ETH":
                return _usd(item.get("prices"))
        return None

    async def get_holdings(self, address: str) -> EthHoldings:
        """ETH and ERC-20 balances held now, with USD values where Alchemy has a price.
        20 + 40 per 25 prices (up to 100 tokens) + 10 per token shown (up to 40): about 600 CU at most."""
        address = address.lower()

        async def fetch():
            summary, balances = await asyncio.gather(
                self.get_address_summary(address), self._rpc("alchemy_getTokenBalances", [address, "erc20"]))
            held = [(lower(b.get("contractAddress")), to_int(b.get("tokenBalance"), 16) or 0)
                    for b in (balances or {}).get("tokenBalances", []) if not b.get("error")]
            held = [(c, raw) for c, raw in held if c and raw > 0]
            try:
                # Prices first, so tokens with a price (real ones) are kept when spam pushes the count past MAX_TOKENS.
                prices = await self._prices_by_address([c for c, _ in held[:MAX_PRICED]]) if held else {}
                eth_price = await self._eth_price()
            except ProviderError:
                prices, eth_price = {}, None  # balances are still worth showing without prices
            held.sort(key=lambda item: item[0] not in prices)
            shown, not_shown = held[:MAX_TOKENS], max(0, len(held) - MAX_TOKENS)
            metadata = await asyncio.gather(*(self._rpc("alchemy_getTokenMetadata", [c]) for c, _ in shown),
                                            return_exceptions=True)
            tokens = []
            for (contract, raw), meta in zip(shown, metadata):
                meta = meta if isinstance(meta, dict) else {}
                decimals = meta.get("decimals")
                decimals = decimals if isinstance(decimals, int) else None
                amount = _decimal_amount(raw, decimals)
                price = prices.get(contract)
                value = float(amount) * price if price is not None and amount is not None else None
                tokens.append(EthTokenHolding(
                    token_contract=contract, token_symbol=plain_text(meta.get("symbol")), token_name=plain_text(meta.get("name")),
                    token_decimals=decimals, balance_raw=str(raw), balance=amount, price_usd=price,
                    value_usd=round(value, 2) if value is not None else None))
            tokens.sort(key=lambda t: (t.value_usd is None, -(t.value_usd or 0)))
            wei = int(summary.balance_wei)
            eth_value = round(wei / 10**18 * eth_price, 2) if eth_price is not None else None
            return EthHoldings(
                address=address, chain=self.chain, eth_balance_wei=str(wei), eth_price_usd=eth_price,
                eth_value_usd=eth_value, tokens=tokens,
                tokens_total_usd=round(sum(t.value_usd or 0 for t in tokens), 2), tokens_not_shown=not_shown,
                provenance=self._provenance(f"alchemy_getTokenBalances + Prices API {address}"))

        return await self._cached(("holdings", address), TTL_HOLDINGS, fetch)

    async def get_nfts(self, address: str) -> EthNftHoldings:
        """The newest 100 NFTs the address holds (one getNFTsForOwner call, 480 CU)."""
        address = address.lower()

        async def fetch():
            url = f"https://{self.slug}.g.alchemy.com/nft/v3/{self._api_key}/getNFTsForOwner"
            body = await self._rest("getNFTsForOwner", "GET", url, params={
                "owner": address, "withMetadata": "true", "pageSize": "100", "orderBy": "transferTime"})
            nfts = []
            for item in body.get("ownedNfts") or []:
                contract = item.get("contract") or {}
                opensea = contract.get("openSeaMetadata") or {}
                image = item.get("image") or {}
                spam = contract.get("isSpam")
                nfts.append(EthNftHolding(
                    token_contract=(contract.get("address") or "").lower(),
                    collection=plain_text(opensea.get("collectionName") or contract.get("name")),
                    standard=contract.get("tokenType") or item.get("tokenType"),
                    token_id=str(item.get("tokenId") or ""), name=plain_text(item.get("name")),
                    image_url=image.get("thumbnailUrl") or image.get("cachedUrl"),
                    quantity=str(item.get("balance") or "1"),
                    is_spam=spam if isinstance(spam, bool) else None,
                    floor_price_eth=_float(opensea.get("floorPrice"))))
            total = body.get("totalCount")
            return EthNftHoldings(
                address=address, chain=self.chain, total_count=total if isinstance(total, int) else None, nfts=nfts,
                spam_count=sum(1 for n in nfts if n.is_spam),
                provenance=self._provenance(f"nft/v3 getNFTsForOwner owner={address}"))

        return await self._cached(("nfts", address), TTL_HOLDINGS, fetch)

    # ---- single transaction -------------------------------------------------------------------

    async def get_transaction(self, tx_hash: str) -> EthTransaction:
        tx_hash = tx_hash.lower()

        async def fetch():
            tx, receipt = await asyncio.gather(self._rpc("eth_getTransactionByHash", [tx_hash]),
                                               self._rpc("eth_getTransactionReceipt", [tx_hash]))
            if not tx:
                raise ProviderError("Alchemy has no record of that transaction", 404)
            block_number = to_int(tx.get("blockNumber"), 16)
            block_time = None
            if block_number is not None:
                block = await self._rpc("eth_getBlockByNumber", [hex(block_number), False])
                block_time = from_unix(to_int((block or {}).get("timestamp"), 16))
            return from_rpc(tx, receipt, block_number, block_time, self._provenance(f"eth_getTransactionByHash {tx_hash}"),
                            self.chain, self.network, self.chain_id)

        return await self._cached(("tx", tx_hash),
                                  lambda t: TTL_CONFIRMED_TX if t.status != "pending" else TTL_RECENT, fetch)

    async def get_tx_internal_transfers(self, tx_hash: str) -> list[EthInternalTransfer]:
        raise ProviderError("Alchemy's free plan has no per-transaction internal transfer list", 501)

    async def health(self) -> ProviderHealth:
        """Latest block number. Never raises."""
        try:
            tip = await self._rpc("eth_blockNumber", [])
            return ProviderHealth(provider=self.name, reachable=True, tip_height=to_int(tip, 16), checked_at=utcnow(),
                                  detail="ok")
        except ProviderError as exc:
            return ProviderHealth(provider=self.name, reachable=False, tip_height=None, checked_at=utcnow(),
                                  detail=exc.message)
