"""The contract every Bitcoin data provider must follow.

Routes depend on this class, not on mempool.space directly, so a second
provider can be added later without touching the routes.
"""
from abc import ABC, abstractmethod

from app.schemas.bitcoin import AddressSummary, ProviderHealth, TransactionSummary


class ProviderError(Exception):
    """Raised when a provider cannot answer. status_code is the HTTP code our API should return."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class BitcoinProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    async def get_address_summary(self, address: str) -> AddressSummary: ...

    @abstractmethod
    async def get_transaction(self, txid: str) -> TransactionSummary: ...

    @abstractmethod
    async def health(self) -> ProviderHealth: ...