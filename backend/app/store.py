"""FastAPI dependencies for saving data. Tests replace get_store with a fake."""
from fastapi import Request

from app.db import NullStore
from app.providers.base import BitcoinProvider
from app.schemas.bitcoin import AddressSummary, ProviderHealth, TransactionSummary


def get_store(request: Request) -> NullStore:
    """The store opened at startup, or NullStore if the app started without one."""
    return getattr(request.app.state, "store", None) or NullStore()


class RecordingProvider(BitcoinProvider):
    """Wraps a provider and keeps a copy of everything it fetched, so it can be saved afterwards."""

    def __init__(self, inner: BitcoinProvider):
        self.inner = inner
        self.name = inner.name
        self.addresses: list[AddressSummary] = []
        self.transactions: list[TransactionSummary] = []

    async def get_address_summary(self, address: str) -> AddressSummary:
        summary = await self.inner.get_address_summary(address)
        self.addresses.append(summary)
        return summary

    async def get_transaction(self, txid: str) -> TransactionSummary:
        tx = await self.inner.get_transaction(txid)
        self.transactions.append(tx)
        return tx

    async def get_address_transactions(self, address: str, after_txid: str | None = None) -> list[TransactionSummary]:
        txs = await self.inner.get_address_transactions(address, after_txid)
        self.transactions.extend(txs)
        return txs

    async def get_block_stats(self, height: int):
        """Block summaries for the network-context features (not saved)."""
        return await self.inner.get_block_stats(height)

    async def health(self) -> ProviderHealth:
        return await self.inner.health()
