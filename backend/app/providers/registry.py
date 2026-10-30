"""Chooses which provider object the routes receive."""
from functools import lru_cache

from fastapi import Depends

from app.config import Settings, get_settings
from app.providers.base import BitcoinProvider
from app.providers.bitcoin_esplora import BlockstreamEsploraProvider
from app.providers.bitcoin_mempool import MempoolSpaceProvider
from app.providers.failover import FailoverBitcoinProvider


def get_bitcoin_provider(settings: Settings = Depends(get_settings)) -> BitcoinProvider:
    """FastAPI dependency. Tests replace this function to inject recorded data.

    Returns the failover chain (mempool.space first, then blockstream.info by default; see
    BTC_PROVIDERS). The same object serves every request, so its cache and cooldowns are shared.
    """
    return shared_bitcoin_provider(
        settings.btc_providers,
        settings.btc_provider_base_url,
        settings.btc_esplora_base_url,
        settings.btc_network,
        settings.http_timeout_seconds,
    )


@lru_cache(maxsize=4)
def shared_bitcoin_provider(names: tuple[str, ...], mempool_url: str, esplora_url: str, network: str,
                            timeout_seconds: float) -> FailoverBitcoinProvider:
    """One failover chain per configuration, for the whole process."""
    builders = {
        "mempool": lambda: MempoolSpaceProvider(mempool_url, network, timeout_seconds),
        "blockstream": lambda: BlockstreamEsploraProvider(esplora_url, network, timeout_seconds),
    }
    return FailoverBitcoinProvider([builders[name]() for name in names])
