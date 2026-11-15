"""Offline tests for reading more than one page of history (BTC_ADDRESS_PAGES, EVM_LIST_PAGES)."""
import asyncio
import json
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx

from app.config import _clamp
from app.providers.ethereum_alchemy import AlchemyProvider
from app.providers.ethereum_etherscan import EtherscanProvider
from app.providers.paging import bitcoin_history

ADDR = "0x" + "a" * 40
OTHER = "0x" + "b" * 40


# ---- Bitcoin: 25 confirmed per page, follow /txs/chain/{last txid} ---------------------------------

class PagedBitcoin:
    def __init__(self, total: int):
        self.txs = [SimpleNamespace(txid=f"{i:064x}", status="confirmed") for i in range(total)]
        self.calls = []

    async def get_address_transactions(self, address, after_txid=None):
        self.calls.append(after_txid)
        start = 0 if after_txid is None else next(i for i, t in enumerate(self.txs) if t.txid == after_txid) + 1
        return self.txs[start:start + 25]


def test_bitcoin_pages_until_a_short_page():
    provider = PagedBitcoin(60)
    txs, complete = asyncio.run(bitcoin_history(provider, "addr", max_pages=4))
    assert len(txs) == 60 and complete and len(provider.calls) == 3


def test_bitcoin_stops_at_max_pages_and_paces():
    provider, paced = PagedBitcoin(500), []

    async def pace():
        paced.append(1)

    txs, complete = asyncio.run(bitcoin_history(provider, "addr", max_pages=4, pace=pace))
    assert len(txs) == 100 and not complete and len(provider.calls) == 4 and len(paced) == 3


# ---- Etherscan: 1,000 rows per page, page=2.. while full, 10,000 rows at most -----------------------

def etherscan_rows(n: int, start: int = 0) -> list[dict]:
    return [{"hash": "0x" + f"{start + i:064x}", "blockNumber": str(10_000 - start - i), "timeStamp": "1700000000",
             "from": ADDR, "to": OTHER, "value": "1", "isError": "0", "txreceipt_status": "1", "gasUsed": "21000",
             "gasPrice": "1", "functionName": "", "methodId": "0x", "contractAddress": ""} for i in range(n)]


def fake_etherscan(total: int, pages_seen: list):
    def handler(request: httpx.Request) -> httpx.Response:
        query = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        page, offset = int(query["page"]), int(query["offset"])
        pages_seen.append(page)
        rows = etherscan_rows(max(0, min(offset, total - (page - 1) * offset)), (page - 1) * offset)
        return httpx.Response(200, json={"status": "1" if rows else "0",
                                         "message": "OK" if rows else "No transactions found", "result": rows})
    return httpx.MockTransport(handler)


def test_etherscan_follows_full_pages():
    pages: list = []
    provider = EtherscanProvider("ETHERSCANTESTKEY", transport=fake_etherscan(2_300, pages), min_interval=0,
                                 max_pages=3)
    txs = asyncio.run(provider.get_address_transactions(ADDR))
    assert len(txs) == 2_300 and pages == [1, 2, 3]
    assert provider.history_complete("get_address_transactions", ADDR, len(txs))


def test_etherscan_marks_history_incomplete_at_the_page_cap():
    pages: list = []
    provider = EtherscanProvider("ETHERSCANTESTKEY", transport=fake_etherscan(5_000, pages), min_interval=0,
                                 max_pages=2)
    txs = asyncio.run(provider.get_address_transactions(ADDR))
    assert len(txs) == 2_000 and pages == [1, 2]
    assert not provider.history_complete("get_address_transactions", ADDR, len(txs))
    # Etherscan refuses page x offset > 10,000, so more than 10 pages is never asked for.
    assert EtherscanProvider("ETHERSCANTESTKEY", max_pages=50).max_pages == 10


# ---- Alchemy: 1,000 transfers per call, follow pageKey ------------------------------------------------

def test_alchemy_follows_page_key():
    calls: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = json.loads(request.content)["params"][0]
        calls.append(params.get("pageKey"))
        page = int(params.get("pageKey") or 0)
        rows = [{"category": "external", "blockNum": hex(5_000 - page * 10 - i), "from": ADDR, "to": OTHER,
                 "hash": "0x" + f"{page * 10 + i:064x}", "uniqueId": f"{page}:{i}", "metadata": {},
                 "rawContract": {"value": "0x1"}, "asset": "ETH"} for i in range(10)] if "fromAddress" in params else []
        result = {"transfers": rows, **({"pageKey": str(page + 1)} if rows else {})}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})

    provider = AlchemyProvider("ALCHEMYTESTKEY99", transport=httpx.MockTransport(handler), cu_per_second=1e9,
                               max_pages=3)
    txs = asyncio.run(provider.get_address_transactions(ADDR))
    # Sent: 3 calls (pageKey still set after the third), received: 1 call with nothing.
    assert len(txs) == 30 and provider.calls_made[(ADDR, "eth")] == 4
    assert not provider.history_complete("get_address_transactions", ADDR, len(txs))


def test_page_settings_are_clamped():
    assert _clamp(None, 3, 1, 10) == 3 and _clamp("50", 3, 1, 10) == 10 and _clamp("x", 3, 1, 10) == 3
