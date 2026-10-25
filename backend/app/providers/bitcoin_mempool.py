"""Bitcoin adapter for the mempool.space REST API (Esplora-style endpoints)."""
from datetime import datetime, timezone

import httpx

from app.providers.base import BitcoinProvider, ProviderError
from app.schemas.bitcoin import (
    ActivityTotals,
    AddressSummary,
    ProviderHealth,
    Provenance,
    TransactionSummary,
    TxEndpoint,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _totals(stats: dict) -> ActivityTotals:
    """Convert one provider stats block (chain_stats or mempool_stats) into our ActivityTotals."""
    funded = int(stats.get("funded_txo_sum", 0))
    spent = int(stats.get("spent_txo_sum", 0))
    return ActivityTotals(
        tx_count=int(stats.get("tx_count", 0)),
        funded_sats=funded,
        spent_sats=spent,
        balance_sats=funded - spent,
    )


class MempoolSpaceProvider(BitcoinProvider):
    name = "mempool.space"

    def __init__(self, base_url: str, network: str, timeout_seconds: float, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url
        self.network = network
        self.timeout_seconds = timeout_seconds
        # transport is only passed in tests, to replay recorded responses without the internet.
        self._transport = transport

    async def _get_json(self, path: str) -> tuple[object, str]:
        """GET base_url + path and return (parsed JSON, full URL). Converts every failure into ProviderError."""
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, transport=self._transport) as client:
                response = await client.get(url, headers={"Accept": "application/json"})
        except httpx.TimeoutException:
            raise ProviderError(f"{self.name} did not answer within {self.timeout_seconds} seconds", 504)
        except httpx.HTTPError as exc:
            raise ProviderError(f"Could not reach {self.name}: {exc.__class__.__name__}", 502)

        if response.status_code == 400 or response.status_code == 404:
            raise ProviderError(f"{self.name} rejected the request: {response.text[:200]}", 404)
        if response.status_code == 429:
            raise ProviderError(f"{self.name} rate limit reached. Wait a minute and try again.", 429)
        if response.status_code >= 400:
            raise ProviderError(f"{self.name} returned HTTP {response.status_code}", 502)
        try:
            return response.json(), url
        except ValueError:
            raise ProviderError(f"{self.name} returned a response that is not JSON", 502)

    async def get_address_summary(self, address: str) -> AddressSummary:
        """Fetch confirmed and unconfirmed totals for one address."""
        data, url = await self._get_json(f"/address/{address}")
        return AddressSummary(
            entity_id=f"bitcoin:{self.network}:address:{address}",
            network=self.network,
            address=address,
            confirmed=_totals(data.get("chain_stats", {})),
            unconfirmed=_totals(data.get("mempool_stats", {})),
            provenance=Provenance(provider=self.name, source_url=url, retrieved_at=_now(), data_mode="live"),
        )

    async def get_transaction(self, txid: str) -> TransactionSummary:
        """Fetch one transaction with its inputs, outputs and confirmation status."""
        data, url = await self._get_json(f"/tx/{txid}")
        status = data.get("status", {})
        confirmed = bool(status.get("confirmed"))
        block_time = status.get("block_time")

        inputs = []
        for vin in data.get("vin", []):
            prevout = vin.get("prevout") or {}  # coinbase inputs have no prevout
            inputs.append(TxEndpoint(address=prevout.get("scriptpubkey_address"), value_sats=prevout.get("value")))
        outputs = [
            TxEndpoint(address=vout.get("scriptpubkey_address"), value_sats=vout.get("value"))
            for vout in data.get("vout", [])
        ]
        return TransactionSummary(
            entity_id=f"bitcoin:{self.network}:tx:{txid}",
            network=self.network,
            txid=txid,
            status="confirmed" if confirmed else "unconfirmed",
            block_height=status.get("block_height"),
            block_time=datetime.fromtimestamp(block_time, tz=timezone.utc) if block_time else None,
            fee_sats=data.get("fee"),
            inputs=inputs,
            outputs=outputs,
            provenance=Provenance(provider=self.name, source_url=url, retrieved_at=_now(), data_mode="live"),
        )

    async def health(self) -> ProviderHealth:
        """Ask for the current block height. Never raises: failures are reported in the result."""
        try:
            height, _ = await self._get_json("/blocks/tip/height")
            return ProviderHealth(provider=self.name, reachable=True, tip_height=int(height), checked_at=_now(), detail="ok")
        except ProviderError as exc:
            return ProviderHealth(provider=self.name, reachable=False, tip_height=None, checked_at=_now(), detail=exc.message)