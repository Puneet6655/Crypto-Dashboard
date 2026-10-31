"""Offline tests for the Alchemy adapter, Ethereum failover (Etherscan -> Alchemy) and Base investigations."""
import asyncio
import json
import logging

import httpx
from fastapi.testclient import TestClient

from app.intelligence import snapshots
from app.intelligence.index import IndicatorIndex, get_indicator_index
from app.main import app
from app.providers.ethereum_alchemy import AlchemyProvider
from app.providers.ethereum_etherscan import EtherscanProvider
from app.providers.failover import FailoverEvmProvider
from app.providers.registry import get_base_provider
from tests.test_indicators import lists_api

KEY = "ALCHEMYTESTKEY99"
ADDR = "0x" + "a" * 40
OTHER = "0x" + "b" * 40
SANCTIONED = "0x8589427373d6d84e98730d7795d8f6f8731fda16"  # in the fake OFAC ETH list of test_indicators
TOKEN = "0x" + "c" * 40
NFT = "0x" + "d" * 40


def transfer(category, frm, to, block, **extra):
    row = {"category": category, "blockNum": hex(block), "from": frm, "to": to, "hash": "0x" + f"{block:064x}",
           "uniqueId": f"{block}:{category}:{frm}", "metadata": {"blockTimestamp": "2026-10-01T00:00:00.000Z"},
           "rawContract": {"value": hex(10**18), "address": None, "decimal": "0x12"}, "asset": "ETH"}
    row.update(extra)
    return row


SENT = [transfer("external", ADDR, SANCTIONED, 105),
        transfer("erc20", ADDR, OTHER, 104, asset="USDC", rawContract={"value": hex(2_500_000), "address": TOKEN, "decimal": "0x6"})]
RECEIVED = [transfer("internal", OTHER, ADDR, 103),
            transfer("erc721", OTHER, ADDR, 102, asset="PUNK", tokenId="0x2a", rawContract={"address": NFT}),
            transfer("erc1155", OTHER, ADDR, 101, asset=None, erc1155Metadata=[{"tokenId": "0x1", "value": "0x3"}],
                     rawContract={"address": NFT})]


def fake_alchemy(calls: list):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith(f"/v2/{KEY}")
        body = json.loads(request.content)
        method, params = body["method"], body["params"]
        calls.append(method)
        result = {
            "eth_getBalance": "0xde0b6b3a7640000",  # 1 ETH
            "eth_getTransactionCount": "0x5",
            "eth_blockNumber": "0x10",
        }.get(method)
        if method == "alchemy_getAssetTransfers":
            rows = SENT if "fromAddress" in params[0] else RECEIVED
            result = {"transfers": [r for r in rows if r["category"] in params[0]["category"]], "pageKey": ""}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})
    return httpx.MockTransport(handler)


def alchemy(calls: list, chain="ethereum") -> AlchemyProvider:
    return AlchemyProvider(KEY, chain, 5, transport=fake_alchemy(calls), cu_per_second=1e9)


def rate_limited_etherscan() -> EtherscanProvider:
    transport = httpx.MockTransport(lambda r: httpx.Response(429, text="Too Many Requests"))
    return EtherscanProvider("ETHERSCANKEY1", "https://etherscan.test/v2/api", 1, 5, transport=transport, min_interval=0)


def test_eth_and_token_transfers_are_fetched_apart():
    """Token spam cannot fill the 1,000-record page and push out ETH transactions: each group has its own calls."""
    calls = []
    p = alchemy(calls)
    txs = asyncio.run(p.get_address_transactions(ADDR))
    assert calls.count("alchemy_getAssetTransfers") == 2  # ETH group only
    assert [t.value_wei for t in txs] == [str(10**18)]
    asyncio.run(p.get_internal_transfers(ADDR))
    assert calls.count("alchemy_getAssetTransfers") == 2  # served from the same ETH pair
    asyncio.run(p.get_token_transfers(ADDR))
    assert calls.count("alchemy_getAssetTransfers") == 4  # the token pair
    assert p.history_complete("get_address_transactions", ADDR, 1)
    assert "external/internal" in p.describe("get_address_transactions", ADDR)[1]


def test_two_pairs_of_transfer_calls_serve_every_list():
    calls = []
    p = alchemy(calls)

    async def everything():
        return await asyncio.gather(p.get_address_transactions(ADDR), p.get_token_transfers(ADDR),
                                    p.get_internal_transfers(ADDR), p.get_nft_transfers(ADDR))

    txs, tokens, internal, nfts = asyncio.run(everything())
    assert calls.count("alchemy_getAssetTransfers") == 4  # ETH pair + token pair; concurrent callers share them
    assert txs[0].status == "unknown" and txs[0].fee_wei is None and txs[0].value_wei == str(10**18)
    assert txs[0].entity_id.startswith("ethereum:mainnet:tx:") and KEY not in txs[0].provenance.source_url
    assert (tokens[0].token_symbol, tokens[0].token_decimals, tokens[0].value_raw) == ("USDC", 6, "2500000")
    assert internal[0].from_address == OTHER
    assert [(n.standard, n.token_id, n.quantity) for n in nfts] == [("ERC-721", "42", "1"), ("ERC-1155", "1", "3")]


def test_failover_from_rate_limited_etherscan_to_alchemy():
    calls = []
    chain = FailoverEvmProvider([rate_limited_etherscan(), alchemy(calls)], chain="ethereum", label="Ethereum")
    summary = asyncio.run(chain.get_address_summary(ADDR))
    assert summary.provenance.provider == "alchemy.com" and summary.balance_wei == str(10**18)
    assert summary.sent_tx_count == 5
    asyncio.run(chain.get_address_transactions(ADDR))
    name, url = chain.describe("get_address_transactions", ADDR)
    assert name == "alchemy.com" and KEY not in url
    assert chain.state["etherscan.io"].cooldown_after_rate_limit


def test_alchemy_key_is_hidden_in_log_lines(caplog):
    with caplog.at_level(logging.INFO, logger="httpx"):
        asyncio.run(alchemy([]).health())
    assert caplog.records and all(KEY not in r.getMessage() for r in caplog.records)


def test_base_investigation_uses_base_ids_and_ofac_note(tmp_path):
    with httpx.Client(transport=lists_api(["1Fake"])) as client:
        snapshots.refresh(tmp_path, client=client)
    base = FailoverEvmProvider([alchemy([], chain="base")], chain="base", label="Base")
    app.dependency_overrides[get_base_provider] = lambda: base
    app.dependency_overrides[get_indicator_index] = lambda: IndicatorIndex(tmp_path)
    try:
        body = TestClient(app).post("/api/investigations", json={
            "mode": "crypto_investigation", "input_type": "base_address", "value": ADDR, "method": "live_lookup",
        }).json()
    finally:
        app.dependency_overrides.pop(get_base_provider, None)
        app.dependency_overrides.pop(get_indicator_index, None)
    assert body["input"]["type"] == "base_address"
    assert any(n["id"] == f"base:mainnet:address:{SANCTIONED}" and n["details"]["listed_in"] for n in body["graph"]["nodes"])
    rules = {i["rule"]: i["matched"] for i in body["indicators"]}
    assert rules == {"ofac_exact_match": False, "ofac_counterparty_match": True}
    assert any("confirm the designation covers Base" in limit for limit in body["limitations"])
    assert any("fees not reported" in f["title"] for f in body["findings"])
    assert body["uncertainty"]["data_sources"] == "alchemy.com"


def test_alchemy_stays_off_until_enabled():
    from dataclasses import replace

    from app.config import get_settings
    from app.providers.registry import get_ethereum_provider

    settings = replace(get_settings(), etherscan_api_key="ETHERSCANKEY1", alchemy_api_key=KEY, alchemy_enabled=False)
    assert get_base_provider(settings) is None
    assert [s.name for s in get_ethereum_provider(settings).sources] == ["etherscan.io"]
    on = replace(settings, alchemy_enabled=True)
    assert [s.name for s in get_ethereum_provider(on).sources] == ["etherscan.io", "alchemy.com"]
    assert [s.name for s in get_base_provider(on).sources] == ["alchemy.com"]
