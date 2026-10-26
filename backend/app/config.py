"""Settings read from environment variables, with safe local defaults."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    btc_provider_base_url: str
    btc_network: str
    http_timeout_seconds: float
    frontend_origin: str


def get_settings() -> Settings:
    """Build a Settings object from environment variables.

    Called once per request by FastAPI's dependency system, so changing an
    environment variable and restarting the server is enough to switch provider.
    """
    return Settings(
        btc_provider_base_url=os.getenv("BTC_PROVIDER_BASE_URL", "https://mempool.space/api").rstrip("/"),
        btc_network=os.getenv("BTC_NETWORK", "mainnet"),
        http_timeout_seconds=float(os.getenv("HTTP_TIMEOUT_SECONDS", "10")),
        frontend_origin=os.getenv("FRONTEND_ORIGIN", "http://localhost:5173"),
    )
