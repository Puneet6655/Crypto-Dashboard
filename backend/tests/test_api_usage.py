"""The API usage meter: counting, quota periods, the providers' hooks and /api/status/usage."""
import asyncio
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.providers.bitcoin_mempool import MempoolSpaceProvider
from app.providers.ethereum_alchemy import AlchemyProvider
from app.providers.ethereum_etherscan import EtherscanProvider
from app.usage import UsageMeter, _cycle_start, meter, path_kind


@pytest.fixture(autouse=True)
def clean_meter():
    meter.reset()
    yield
    meter.reset()


def by_id(summary):
    return {s["id"]: s for s in summary["sources"]}


def test_counts_today_and_month_against_the_free_quota():
    days = iter([date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 4), date(2026, 11, 4), date(2026, 11, 4)])
    current = {"day": date(2026, 11, 3)}

    def today():
        current["day"] = next(days, current["day"])
        return current["day"]

    m = UsageMeter(today=today)
    m.record("alchemy", "ethereum alchemy_getAssetTransfers", 120)  # 3 Nov
    m.record("alchemy", "solana getSignaturesForAddress", 40)  # 4 Nov
    m.record("etherscan", "account.txlist")
    m.record("etherscan", "account.txlist", ok=False)
    s = by_id(m.summary(days=3))
    assert s["alchemy"]["used"] == 160 and s["alchemy"]["today"]["units"] == 40
    assert s["alchemy"]["percent"] == round(160 / 30_000_000 * 100, 2)
    assert s["alchemy"]["by_kind"][0]["kind"] == "ethereum alchemy_getAssetTransfers"
    assert s["etherscan"]["used"] == 2 and s["etherscan"]["today"]["errors"] == 1
    assert [d["units"] for d in s["alchemy"]["daily"]] == [0, 120, 40]
    assert s["mempool.space"]["quota"] is None and s["mempool.space"]["percent"] is None


def test_alchemy_month_can_start_on_the_billing_day():
    assert _cycle_start(date(2026, 11, 4), 1) == date(2026, 11, 1)
    assert _cycle_start(date(2026, 11, 4), 15) == date(2026, 10, 15)
    assert _cycle_start(date(2026, 11, 20), 15) == date(2026, 11, 15)
    assert _cycle_start(date(2026, 3, 1), 31) == date(2026, 2, 28)


def test_path_kind_drops_addresses_and_heights():
    assert path_kind("/address/bc1qxyz0123456789abcdefghij/txs/chain") == "address/txs/chain"
    assert path_kind("/blocks/tip/height") == "blocks/tip/height"
    assert path_kind("/v1/blocks/870000") == "v1/blocks"


def test_providers_record_each_http_call():
    def etherscan(request):
        return httpx.Response(200, json={"jsonrpc": "2.0", "result": "0x10"})

    def alchemy(request):
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x10"})

    def mempool(request):
        return httpx.Response(429, text="slow down")

    async def run():
        await EtherscanProvider("K", "https://api.etherscan.io/v2/api", 1, transport=httpx.MockTransport(etherscan),
                                min_interval=0).health()
        await AlchemyProvider("ALCHEMYKEY", transport=httpx.MockTransport(alchemy)).health()
        await MempoolSpaceProvider("https://mempool.space/api", "mainnet", 5,
                                   transport=httpx.MockTransport(mempool)).health()

    asyncio.run(run())
    s = by_id(meter.summary())
    assert s["etherscan"]["today"] == {"calls": 1, "units": 1, "errors": 0}
    assert s["etherscan"]["by_kind"][0]["kind"] == "proxy.eth_blockNumber"
    assert s["alchemy"]["today"]["units"] == 10  # eth_blockNumber costs 10 CU
    assert s["mempool.space"]["today"]["errors"] == 1


def test_usage_route():
    meter.record("etherscan", "account.balance")
    body = TestClient(app).get("/api/status/usage?days=7").json()
    s = by_id(body)
    assert s["etherscan"]["today"]["calls"] == 1 and len(s["etherscan"]["daily"]) == 7
    assert s["etherscan"]["quota"] == 100_000 and s["alchemy"]["unit"] == "CU"
    assert body["saved"] is False
