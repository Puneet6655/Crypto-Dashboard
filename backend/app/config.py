"""Settings read from environment variables, with safe local defaults."""
import os
from dataclasses import dataclass, field
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
    # Trained models, registry.json and scoring_rows.parquet from training/model_registry.py (gitignored).
    models_dir: Path = BACKEND_DIR / "models"
    # Load the live XGBoost model at startup so Bitcoin address lookups include its score (MODELS_PRELOAD=false to skip).
    models_preload: bool = True
    # Ethereum lookups (Etherscan API V2). Empty key = Ethereum lookups are switched off.
    # repr=False keeps the key out of logs and error pages that print the settings.
    etherscan_api_key: str | None = field(default=None, repr=False)
    etherscan_base_url: str = "https://api.etherscan.io/v2/api"
    eth_chain_id: int = 1  # Ethereum mainnet (Base, 8453, is not in Etherscan's free plan)
    # Alchemy: backup for Ethereum and the only source for Base. The endpoint URL contains the key.
    alchemy_api_key: str | None = field(default=None, repr=False)
    alchemy_endpoint: str | None = field(default=None, repr=False)
    # Off until the user approves using Alchemy (2026-10-31): the key alone does not switch it on.
    alchemy_enabled: bool = False
    # Day of the month the Alchemy plan's monthly compute-unit allowance starts again (1-28).
    alchemy_billing_day: int = 1
    # Live alerts over Alchemy's WebSocket for case and OFAC addresses (needs alchemy_enabled).
    live_alerts_enabled: bool = True
    live_alerts_watch_ofac: bool = True
    # Advanced graph mode: its own daily share of the Alchemy free plan (30M CU a month), and the
    # largest node cap the browser may ask for.
    graph_alchemy_daily_cu: int = 300_000
    graph_max_nodes: int = 20_000


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
        models_dir=Path(os.getenv("MODELS_DIR") or BACKEND_DIR / "models"),
        models_preload=os.getenv("MODELS_PRELOAD", "true").strip().lower() not in ("0", "false", "no", "off"),
        etherscan_api_key=(os.getenv("ETHERSCAN_API_KEY") or "").strip() or None,
        etherscan_base_url=os.getenv("ETHERSCAN_BASE_URL", "https://api.etherscan.io/v2/api").rstrip("/"),
        eth_chain_id=int(os.getenv("ETH_CHAIN_ID", "1")),
        **_alchemy_settings(os.getenv("ALCHEMY_API_KEY"), os.getenv("ALCHEMY_ENDPOINT")),
        alchemy_enabled=os.getenv("ALCHEMY_ENABLED", "false").strip().lower() in ("1", "true", "yes"),
        alchemy_billing_day=int(os.getenv("ALCHEMY_BILLING_DAY", "1") or 1),
        live_alerts_enabled=os.getenv("LIVE_ALERTS_ENABLED", "true").strip().lower() not in ("0", "false", "no"),
        live_alerts_watch_ofac=os.getenv("LIVE_ALERTS_WATCH_OFAC", "true").strip().lower() not in ("0", "false", "no"),
        graph_alchemy_daily_cu=int(os.getenv("GRAPH_ALCHEMY_DAILY_CU", "300000")),
        graph_max_nodes=int(os.getenv("GRAPH_MAX_NODES", "20000")),
    )


def _alchemy_settings(key: str | None, endpoint: str | None) -> dict:
    """Accept the key, the endpoint (https://eth-mainnet.g.alchemy.com/v2/<key>), or both."""
    key = (key or "").strip() or None
    endpoint = (endpoint or "").strip().rstrip("/") or None
    if endpoint and "/v2/" in endpoint and not key:
        key = endpoint.rsplit("/v2/", 1)[1] or None
    if endpoint and key and endpoint.endswith("/v2"):
        endpoint = f"{endpoint}/{key}"
    return {"alchemy_api_key": key, "alchemy_endpoint": endpoint}
