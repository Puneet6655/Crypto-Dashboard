"""Chooses which provider object the routes receive."""
from functools import lru_cache

from fastapi import Depends

from app.config import Settings, get_settings
from app.providers.base import BitcoinProvider
from app.providers.bitcoin_esplora import BlockstreamEsploraProvider
from app.providers.bitcoin_mempool import MempoolSpaceProvider
from app.providers.ethereum_alchemy import AlchemyProvider
from app.providers.ethereum_etherscan import EtherscanProvider
from app.providers.failover import FailoverBitcoinProvider, FailoverEvmProvider
from app.providers.solana_alchemy import SolanaProvider


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


def get_ethereum_provider(settings: Settings = Depends(get_settings)) -> FailoverEvmProvider | None:
    """FastAPI dependency: Ethereum sources in failover order (Etherscan, then Alchemy when
    ALCHEMY_ENABLED=true), or None when no source is available."""
    alchemy_key = settings.alchemy_api_key if settings.alchemy_enabled else None
    if not settings.etherscan_api_key and not alchemy_key:
        return None
    return shared_evm_provider("ethereum", settings.etherscan_api_key, settings.etherscan_base_url,
                               settings.eth_chain_id, alchemy_key, settings.alchemy_endpoint,
                               settings.http_timeout_seconds)


def get_base_provider(settings: Settings = Depends(get_settings)) -> FailoverEvmProvider | None:
    """FastAPI dependency: Base through Alchemy (Etherscan's free plan has no Base), or None unless
    ALCHEMY_API_KEY is set and ALCHEMY_ENABLED=true."""
    if not settings.alchemy_api_key or not settings.alchemy_enabled:
        return None
    return shared_evm_provider("base", None, None, None, settings.alchemy_api_key, None,
                               settings.http_timeout_seconds)


@lru_cache(maxsize=4)
def shared_evm_provider(chain: str, etherscan_key: str | None, etherscan_url: str | None, chain_id: int | None,
                        alchemy_key: str | None, alchemy_endpoint: str | None,
                        timeout_seconds: float) -> FailoverEvmProvider:
    """One failover chain per network for the whole process, so pacing, cooldowns and caches are shared."""
    sources = []
    if etherscan_key:
        sources.append(EtherscanProvider(etherscan_key, etherscan_url, chain_id, timeout_seconds))
    if alchemy_key:
        # Use the pasted endpoint only when it is for this network; otherwise build it from the key.
        slug = "eth-mainnet" if chain == "ethereum" else "base-mainnet"
        url = alchemy_endpoint if alchemy_endpoint and f"//{slug}." in alchemy_endpoint else None
        sources.append(AlchemyProvider(alchemy_key, chain, timeout_seconds, url=url))
    label = {"ethereum": "Ethereum", "base": "Base"}[chain]
    return FailoverEvmProvider(sources, chain=chain, label=label)


def get_solana_provider(settings: Settings = Depends(get_settings)) -> SolanaProvider | None:
    """FastAPI dependency: Solana through Alchemy, or None unless ALCHEMY_API_KEY is set and ALCHEMY_ENABLED=true."""
    if not settings.alchemy_api_key or not settings.alchemy_enabled:
        return None
    return shared_solana_provider(settings.alchemy_api_key, settings.http_timeout_seconds)


@lru_cache(maxsize=2)
def shared_solana_provider(api_key: str, timeout_seconds: float) -> SolanaProvider:
    """One Solana source for the whole process, so its pacing and cache are shared."""
    return SolanaProvider(api_key, timeout_seconds)
