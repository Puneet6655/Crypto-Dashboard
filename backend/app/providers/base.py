"""The contract every Bitcoin data provider must follow.

Routes depend on this class, not on mempool.space directly, so a second
provider can be added later without touching the routes.
"""
from abc import ABC, abstractmethod

from app.schemas.bitcoin import AddressSummary, ProviderHealth, TransactionSummary


class ProviderError(Exception):
    """Raised when a provider cannot answer. status_code is the HTTP code our API should return.

    retry_after (seconds) is set when we know how long the caller should wait.
    """

    def __init__(self, message: str, status_code: int = 502, retry_after: float | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.retry_after = retry_after

    def headers(self) -> dict[str, str] | None:
        """HTTP headers for our API's error response (Retry-After when known)."""
        if self.retry_after is None:
            return None
        return {"Retry-After": str(max(1, int(self.retry_after + 0.999)))}


class BitcoinProvider(ABC):
    name: str = "abstract"
    # Method names this provider cannot serve; the failover chain skips it for those.
    unsupported: frozenset[str] = frozenset()

    @abstractmethod
    async def get_address_summary(self, address: str) -> AddressSummary: ...

    @abstractmethod
    async def get_transaction(self, txid: str) -> TransactionSummary: ...

    @abstractmethod
    async def get_address_transactions(self, address: str, after_txid: str | None = None) -> list[TransactionSummary]: ...

    @abstractmethod
    async def health(self) -> ProviderHealth: ...
