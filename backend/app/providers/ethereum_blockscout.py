"""Ethereum adapter for the Blockscout PRO API (https://api.blockscout.com), a backup for Etherscan.

Needs BLOCKSCOUT_API_KEY in backend/.env (free key from https://dev.blockscout.com). One key covers
every chain Blockscout runs, but since 1 Oct 2026 the free plan no longer includes Base, Polygon or
zkSync, so the dashboard uses it for Ethereum only; Base stays on Alchemy.

Blockscout copies Etherscan's account API (module=account&action=txlist ...), so this class reuses the
Etherscan adapter and changes only what differs:
- the chain goes in `chain_id` (Etherscan: `chainid`) on https://api.blockscout.com/v2/api;
- there is no module=proxy: node calls (eth_getTransactionByHash and so on) are JSON-RPC POSTs to
  https://api.blockscout.com/{chain_id}/json-rpc with the key in an Authorization header;
- the free plan allows 5 requests per second and 100,000 credits a day (most calls cost 20 credits,
  so about 5,000 calls). Usage is counted in credits.

The key never appears in saved or shown URLs (provenance is built without it) and is redacted from
httpx's log lines (evm_common.register_secret).
"""
from __future__ import annotations

import httpx

from app.providers.base import ProviderError
from app.providers.ethereum_etherscan import EtherscanProvider
from app.usage import meter

BLOCKSCOUT_BASE_URL = "https://api.blockscout.com"
MIN_SECONDS_BETWEEN_CALLS = 0.22  # free plan: 5 requests per second, kept a little under
CREDITS_PER_CALL = 20  # Blockscout's default cost per call (a few endpoints cost up to 50)
DAILY_LIMIT_PAUSE_SECONDS = 3600.0

# module=proxy actions (as the Etherscan adapter sends them) -> JSON-RPC params.
_RPC_PARAMS = {
    "eth_blockNumber": lambda p: [],
    "eth_getTransactionCount": lambda p: [p["address"], p.get("tag", "latest")],
    "eth_getTransactionByHash": lambda p: [p["txhash"]],
    "eth_getTransactionReceipt": lambda p: [p["txhash"]],
    "eth_getBlockByNumber": lambda p: [p["tag"], str(p.get("boolean", "false")).lower() == "true"],
}


class BlockscoutProvider(EtherscanProvider):
    name = "blockscout.com"
    label = "Blockscout"
    usage_key = "blockscout"
    key_env = "BLOCKSCOUT_API_KEY"
    chain_param = "chain_id"
    rate_text = "5 requests per second"

    def __init__(self, api_key: str, base_url: str = BLOCKSCOUT_BASE_URL, chain_id: int = 1,
                 timeout_seconds: float = 10, transport: httpx.AsyncBaseTransport | None = None,
                 min_interval: float = MIN_SECONDS_BETWEEN_CALLS):
        self.root_url = base_url.rstrip("/")
        super().__init__(api_key, f"{self.root_url}/v2/api", chain_id, timeout_seconds, transport, min_interval)

    def rpc_url(self) -> str:
        return f"{self.root_url}/{self.chain_id}/json-rpc"

    def _meter(self, kind: str, ok: bool = True) -> None:
        meter.record(self.usage_key, kind, units=CREDITS_PER_CALL, ok=ok)

    async def _get(self, params: dict) -> object:
        if params.get("module") == "proxy":
            return await self._rpc(params)
        return await super()._get(params)

    async def _rpc(self, params: dict) -> object:
        method = params["action"]
        if method not in _RPC_PARAMS:
            raise ProviderError(f"Blockscout has no node call {method}", 501)
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": _RPC_PARAMS[method](params)}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, transport=self._transport) as client:
                response = await client.post(self.rpc_url(), json=payload, headers={
                    "Accept": "application/json", "Authorization": f"Bearer {self._api_key}"})
        except httpx.TimeoutException:
            raise ProviderError(f"Blockscout did not answer within {self.timeout_seconds} seconds", 504)
        except httpx.HTTPError as exc:
            raise ProviderError(f"Could not reach Blockscout: {exc.__class__.__name__}", 502)
        self._check_status(response)
        try:
            body = response.json()
        except ValueError:
            raise ProviderError("Blockscout returned a response that is not JSON", 502)
        if not isinstance(body, dict):
            raise ProviderError("Blockscout returned an unexpected response", 502)
        if body.get("error"):
            error = body["error"]
            message = error.get("message", "") if isinstance(error, dict) else str(error)
            raise ProviderError(f"Blockscout node error: {str(message)[:200]}", 502)
        return body.get("result")

    def _check_status(self, response: httpx.Response) -> None:
        if response.status_code == 401:
            raise ProviderError("Blockscout rejected the API key (check BLOCKSCOUT_API_KEY in backend/.env)", 502)
        if response.status_code == 402:
            raise ProviderError("Blockscout's daily credit limit was reached", 429,
                                retry_after=DAILY_LIMIT_PAUSE_SECONDS)
        if response.status_code == 429:
            raise ProviderError("Blockscout rate limit reached", 429)
        if response.status_code >= 400:
            raise ProviderError(f"Blockscout returned HTTP {response.status_code}", 502)
