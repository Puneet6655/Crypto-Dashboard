"""Chooses which provider object the routes receive."""
from fastapi import Depends

from app.config import Settings, get_settings
from app.providers.base import BitcoinProvider
from app.providers.bitcoin_mempool import MempoolSpaceProvider


def get_bitcoin_provider(settings: Settings = Depends(get_settings)) -> BitcoinProvider:
    """FastAPI dependency. Tests replace this function to inject recorded data."""
    return MempoolSpaceProvider(
        base_url=settings.btc_provider_base_url,
        network=settings.btc_network,
        timeout_seconds=settings.http_timeout_seconds,
    )