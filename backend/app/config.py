"""Settings read from environment variables, with safe local defaults."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Read backend/.env if it exists. Real environment variables win over the file.
load_dotenv(BACKEND_DIR / ".env", override=False)

# Bitcoin data sources the backend knows, in the default order it tries them.
KNOWN_BTC_PROVIDERS = ("mempool", "blockstream")


@dataclass(frozen=True)
class Settings:
    btc_provider_base_url: str
    btc_network: str
    http_timeout_seconds: float
    frontend_origins: list[str]
    max_graph_nodes: int
    database_url: str | None
    # Failover order, e.g. ("mempool", "blockstream"). The first one that answers wins.
    btc_providers: tuple[str, ...] = KNOWN_BTC_PROVIDERS
    btc_esplora_base_url: str = "https://blockstream.info/api"
    # Downloaded OFAC / Ransomwhere snapshots (gitignored, refreshed once a day).
    indicators_dir: Path = BACKEND_DIR / "data" / "raw" / "indicators"
    indicators_auto_refresh: bool = True
    indicators_max_age_hours: float = 24.0


def _provider_list(value: str) -> tuple[str, ...]:
    names = tuple(name.strip().lower() for name in value.split(",") if name.strip())
    unknown = [name for name in names if name not in KNOWN_BTC_PROVIDERS]
    if unknown or not names:
        raise ValueError(f"BTC_PROVIDERS must list some of {', '.join(KNOWN_BTC_PROVIDERS)}; got {value!r}")
    return names


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
        btc_providers=_provider_list(os.getenv("BTC_PROVIDERS", ",".join(KNOWN_BTC_PROVIDERS))),
        btc_esplora_base_url=os.getenv("BTC_ESPLORA_BASE_URL", "https://blockstream.info/api").rstrip("/"),
        indicators_dir=Path(os.getenv("INDICATORS_DIR") or BACKEND_DIR / "data" / "raw" / "indicators"),
        indicators_auto_refresh=os.getenv("INDICATORS_AUTO_REFRESH", "true").strip().lower() not in ("0", "false", "no"),
        indicators_max_age_hours=float(os.getenv("INDICATORS_MAX_AGE_HOURS", "24")),
    )
