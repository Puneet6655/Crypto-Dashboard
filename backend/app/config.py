"""Settings read from environment variables, with safe local defaults."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Read backend/.env if it exists. Real environment variables win over the file.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)


@dataclass(frozen=True)
class Settings:
    btc_provider_base_url: str
    btc_network: str
    http_timeout_seconds: float
    frontend_origins: list[str]
    max_graph_nodes: int
    database_url: str | None


def get_settings() -> Settings:
    """Build a Settings object from environment variables.

    Called once per request by FastAPI's dependency system, so changing an
    environment variable and restarting the server is enough to switch provider.
    """
    return Settings(
        btc_provider_base_url=os.getenv("BTC_PROVIDER_BASE_URL", "https://mempool.space/api").rstrip("/"),
        btc_network=os.getenv("BTC_NETWORK", "mainnet"),
        http_timeout_seconds=float(os.getenv("HTTP_TIMEOUT_SECONDS", "10")),
        # Comma-separated list, e.g. "http://localhost:5173,https://my-app.vercel.app"
        frontend_origins=[
            origin.strip().rstrip("/")
            for origin in os.getenv("FRONTEND_ORIGIN", "http://localhost:5173").split(",")
            if origin.strip()
        ],
        max_graph_nodes=int(os.getenv("MAX_GRAPH_NODES", "50")),
        # Postgres (Neon) connection string. Empty means "do not save data".
        database_url=os.getenv("DATABASE_URL") or None,
    )
