"""Offline tests for Alchemy token holdings (Token + Prices APIs) and NFT holdings (NFT API v3)."""
import asyncio
import json

import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.providers.ethereum_alchemy import AlchemyProvider
from app.providers.failover import FailoverEvmProvider
from app.providers.registry import get_ethereum_provider

KEY = "ALCHEMYTESTKEY99"
ADDR = "0x" + "a" * 40
USDC = "0x" + "c" * 40
JUNK = "0x" + "e" * 40
NFT = "0x" + "d" * 40


def handler(calls: list):
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "/prices/v1/" in path and path.endswith("/tokens/by-address"):
            calls.append("prices")
            body = json.loads(request.content)
            data = [{"network": a["network"], "address": a["address"],
                     "prices": [{"currency": "usd", "value": "1.0001"}] if a["address"] == USDC else [],
                     "error": None if a["address"] == USDC else "Token not found"} for a in body["addresses"]]
            return httpx.Response(200, json={"data": data})
        if path.endswith("/tokens/by-symbol"):
            calls.append("eth_price")
            return httpx.Response(200, json={"data": [{"symbol": "ETH", "prices": [{"currency": "usd", "value": "3000"}]}]})
        if path.endswith("/getNFTsForOwner"):
            calls.append("nfts")
            assert request.url.params["owner"] == ADDR
            return httpx.Response(200, json={"totalCount": 2, "pageKey": None, "ownedNfts": [
                {"contract": {"address": NFT, "name": "Punks", "tokenType": "ERC721", "isSpam": False,
                              "openSeaMetadata": {"collectionName": "CryptoPunks", "floorPrice": 40.5}},
                 "tokenId": "42", "name": "Punk #42", "image": {"thumbnailUrl": "https://img.test/42.png"}, "balance": "1"},
                {"contract": {"address": JUNK, "name": "Claim $FREE", "tokenType": "ERC1155", "isSpam": True},
                 "tokenId": "1", "name": "Visit scam.example to claim", "balance": "3"},
            ]})
        body = json.loads(request.content)
        method, params = body["method"], body["params"]
        calls.append(method)
        result = {
            "eth_getBalance": hex(2 * 10**18),
            "eth_getTransactionCount": "0x1",
            "alchemy_getTokenBalances": {"address": ADDR, "tokenBalances": [
                {"contractAddress": JUNK, "tokenBalance": hex(10**21)},  # spam listed first
                {"contractAddress": USDC, "tokenBalance": hex(2_500_000)},
                {"contractAddress": "0x" + "f" * 40, "tokenBalance": "0x0"},  # zero balances are dropped
            ]},
        }.get(method)
        if method == "alchemy_getTokenMetadata":
            result = {"symbol": "USDC", "name": "USD Coin", "decimals": 6} if params[0] == USDC else \
                     {"symbol": "JUNK", "name": "Junk", "decimals": 18}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})
    return httpx.MockTransport(handle)


def provider(calls):
    return AlchemyProvider(KEY, "ethereum", 5, transport=handler(calls), cu_per_second=1e9)


def test_holdings_values_priced_tokens_and_keeps_unpriced_ones_last():
    calls = []
    h = asyncio.run(provider(calls).get_holdings(ADDR))
    assert h.eth_balance_wei == str(2 * 10**18) and h.eth_price_usd == 3000 and h.eth_value_usd == 6000
    assert [t.token_symbol for t in h.tokens] == ["USDC", "JUNK"]
    usdc, junk = h.tokens
    assert usdc.balance == "2.5" and usdc.value_usd == 2.5
    assert junk.balance == "1000" and junk.price_usd is None and junk.value_usd is None
    assert h.tokens_total_usd == 2.5 and h.tokens_not_shown == 0
    assert KEY not in h.provenance.source_url
    assert calls.count("prices") == 1


def test_holdings_are_cached():
    calls = []
    p = provider(calls)
    asyncio.run(p.get_holdings(ADDR))
    before = len(calls)
    asyncio.run(p.get_holdings(ADDR))
    assert len(calls) == before


def test_nfts_carry_spam_flag_and_floor_price():
    n = asyncio.run(provider([]).get_nfts(ADDR))
    assert n.total_count == 2 and n.spam_count == 1
    punk, junk = n.nfts
    assert (punk.collection, punk.token_id, punk.floor_price_eth, punk.is_spam) == ("CryptoPunks", "42", 40.5, False)
    assert punk.image_url == "https://img.test/42.png"
    assert junk.is_spam is True and junk.quantity == "3"


def test_routes_need_alchemy_and_serve_holdings():
    from app.providers.ethereum_etherscan import EtherscanProvider
    etherscan = EtherscanProvider("ETHERSCANKEY1", "https://etherscan.test/v2/api", 1, 5, min_interval=0)
    etherscan_only = FailoverEvmProvider([etherscan], chain="ethereum", label="Ethereum")
    with_alchemy = FailoverEvmProvider([provider([])], chain="ethereum", label="Ethereum")
    client = TestClient(app)
    try:
        app.dependency_overrides[get_ethereum_provider] = lambda: etherscan_only
        r = client.get(f"/api/ethereum/address/{ADDR}/tokens")
        assert r.status_code == 503 and "ALCHEMY_ENABLED" in r.json()["detail"]
        app.dependency_overrides[get_ethereum_provider] = lambda: with_alchemy
        assert client.get(f"/api/ethereum/address/{ADDR.upper().replace('0X', '0x')}/tokens").json()["eth_value_usd"] == 6000
        assert client.get(f"/api/ethereum/address/{ADDR}/nfts").json()["spam_count"] == 1
        assert client.get("/api/ethereum/address/0x123/nfts").status_code == 422
    finally:
        app.dependency_overrides.pop(get_ethereum_provider, None)


def test_priced_tokens_are_kept_when_spam_fills_the_list(monkeypatch):
    from app.providers import ethereum_alchemy
    monkeypatch.setattr(ethereum_alchemy, "MAX_TOKENS", 1)
    h = asyncio.run(provider([]).get_holdings(ADDR))
    # Alchemy lists JUNK first; the priced token is kept anyway.
    assert [t.token_symbol for t in h.tokens] == ["USDC"] and h.tokens_not_shown == 1
