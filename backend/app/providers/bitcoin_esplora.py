"""Bitcoin adapter for Blockstream's public Esplora API (https://blockstream.info/api).

mempool.space runs an extended copy of the same Esplora REST API, so the address, transaction,
paging and tip-height routes answer with the same JSON. This adapter therefore reuses
MempoolSpaceProvider's request and conversion code unchanged; only the name and the
mempool.space-only routes differ. Reusing the converter keeps every TransactionSummary (and so
every wallet feature) identical whichever of the two answered.
"""
from app.features.wallet_window import BlockStat
from app.providers.base import ProviderError
from app.providers.bitcoin_mempool import MempoolSpaceProvider


class BlockstreamEsploraProvider(MempoolSpaceProvider):
    name = "blockstream.info"
    # /v1/blocks (per-block fee totals) exists only on mempool.space.
    unsupported = frozenset({"get_block_stats"})

    async def get_block_stats(self, height: int) -> list[BlockStat]:
        raise ProviderError(f"{self.name} has no per-block fee totals (mempool.space only)", 501)
