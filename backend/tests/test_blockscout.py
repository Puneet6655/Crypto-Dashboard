"""Offline tests for the Blockscout adapter (Ethereum backup) and its place in the failover chain."""
import asyncio
import json

import httpx

from app.config import get_settings
from app.providers.base import ProviderError
from app.providers.ethereum_blockscout import BlockscoutProvider
from app.providers.ethereum_etherscan import EtherscanProvider
from app.providers.failover import FailoverEvmProvider
from app.providers.registry import get_base_provider, get_ethereum_provider, shared_evm_provider
from app.usage import SOURCES, UsageMeter

KEY = "proapi_TESTKEY"
ADDR = "0x" + "a" * 40
OTHER = "0x" + "b" * 40
TX = "0x" + "1" * 64


def fake_blockscout(state: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        state.setdefault("urls", []).append(str(request.url))
        if request.method == "POST":
            body = json.loads(request.content)
            state.setdefault("rpc", []).append((request.url.path, body["method"], body["params"],
                                                request.headers.get("authorization")))
            results = {
                "eth_blockNumber": "0x10",
                "eth_getTransactionCount": "0x5",
                "eth_getTransactionByHash": {"hash": TX, "from": ADDR, "to": OTHER, "value": "0xde0b6b3a7640000",
                                             "blockNumber": "0x10", "gas": "0x5208", "gasPrice": "0x3b9aca00",
                                             "input": "0x", "nonce": "0x1"},
                "eth_getTransactionReceipt": {"status": "0x1", "gasUsed": "0x5208", "effectiveGasPrice": "0x3b9aca00",
                                              "logs": []},
                "eth_getBlockByNumber": {"timestamp": "0x6553f100"},
            }
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": results[body["method"]]})
        q = dict(request.url.params)
        state.setdefault("params", []).append(q)
        if state.get("status") is not None:
            return httpx.Response(state["status"], json={})
        if q.get("action") == "balance":
            return httpx.Response(200, json={"status": "1", "message": "OK", "result": "1000000000000000000"})
        if q.get("action") == "txlist":
            return httpx.Response(200, json={"status": "1", "message": "OK", "result": [{
                "hash": TX, "blockNumber": "16", "timeStamp": "1700000000", "from": ADDR, "to": OTHER,
                "value": "5", "gasUsed": "21000", "gasPrice": "1", "isError": "0", "txreceipt_status": "1",
                "contractAddress": ""}]})
        return httpx.Response(200, json={"status": "0", "message": "No transactions found", "result": []})
    return handler


def provider(state):
    return BlockscoutProvider(KEY, transport=httpx.MockTransport(fake_blockscout(state)), min_interval=0)


def test_account_calls_use_chain_id_and_keep_the_key_out_of_provenance():
    state = {}
    p = provider(state)
    summary = asyncio.run(p.get_address_summary(ADDR))
    assert summary.balance_wei == "1000000000000000000"
    assert summary.sent_tx_count == 5
    assert summary.provenance.provider == "blockscout.com"
    assert "chain_id=1" in summary.provenance.source_url and "chainid" not in summary.provenance.source_url
    assert KEY not in summary.provenance.source_url
    assert state["params"][0]["apikey"] == KEY and state["params"][0]["chain_id"] == "1"
    assert all(u.startswith("https://api.blockscout.com/") for u in state["urls"])
    # The nonce came from JSON-RPC, with the key in the Authorization header, not the URL.
    path, method, params, auth = state["rpc"][0]
    assert (path, method, params, auth) == ("/1/json-rpc", "eth_getTransactionCount", [ADDR, "latest"], f"Bearer {KEY}")


def test_transactions_and_empty_lists():
    p = provider({})
    txs = asyncio.run(p.get_address_transactions(ADDR))
    assert len(txs) == 1 and txs[0].tx_hash == TX and txs[0].fee_wei == "21000"
    assert asyncio.run(p.get_token_transfers(ADDR)) == []
    assert p.describe("get_address_transactions", ADDR)[0] == "blockscout.com"


def test_transaction_lookup_goes_through_json_rpc():
    state = {}
    tx = asyncio.run(provider(state).get_transaction(TX))
    assert tx.tx_hash == TX and tx.status == "success"
    assert [m for _, m, _, _ in state["rpc"]] == ["eth_getTransactionByHash", "eth_getTransactionReceipt",
                                                   "eth_getBlockByNumber"]
    assert state["rpc"][2][2] == ["0x10", False]


def test_errors_name_blockscout_and_daily_credit_limit_pauses():
    for status, code in ((429, 429), (401, 502), (402, 429), (500, 502)):
        p = provider({"status": status})
        try:
            asyncio.run(p.get_token_transfers(ADDR))
        except ProviderError as exc:
            assert exc.status_code == code and "Blockscout" in exc.message
            if status == 401:
                assert "BLOCKSCOUT_API_KEY" in exc.message
            if status == 402:
                assert p._paused_until > 0
        else:
            raise AssertionError(f"HTTP {status} should raise")


def test_health():
    health = asyncio.run(provider({}).health())
    assert health.reachable and health.tip_height == 16 and health.provider == "blockscout.com"


def test_failover_from_etherscan_to_blockscout():
    def etherscan_down(request):
        return httpx.Response(503, json={})
    etherscan = EtherscanProvider("ESKEY", transport=httpx.MockTransport(etherscan_down), min_interval=0)
    chain = FailoverEvmProvider([etherscan, provider({})], chain="ethereum", label="Ethereum")
    txs = asyncio.run(chain.get_address_transactions(ADDR))
    assert len(txs) == 1
    assert chain.describe("get_address_transactions", ADDR)[0] == "blockscout.com"


def test_registry_order_and_base_stays_on_alchemy(monkeypatch):
    shared_evm_provider.cache_clear()
    for name in ("ETHERSCAN_API_KEY", "ALCHEMY_API_KEY", "ALCHEMY_ENDPOINT", "BLOCKSCOUT_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ALCHEMY_ENABLED", "false")
    assert get_ethereum_provider(get_settings()) is None
    monkeypatch.setenv("BLOCKSCOUT_API_KEY", KEY)
    assert [s.name for s in get_ethereum_provider(get_settings()).sources] == ["blockscout.com"]
    monkeypatch.setenv("ETHERSCAN_API_KEY", "ESKEY")
    monkeypatch.setenv("ALCHEMY_API_KEY", "ALKEY")
    monkeypatch.setenv("ALCHEMY_ENABLED", "true")
    settings = get_settings()
    assert [s.name for s in get_ethereum_provider(settings).sources] == ["etherscan.io", "blockscout.com", "alchemy.com"]
    assert [s.name for s in get_base_provider(settings).sources] == ["alchemy.com"]
    assert "proapi" not in repr(settings)
    shared_evm_provider.cache_clear()


def test_usage_counts_credits():
    assert SOURCES["blockscout"]["unit"] == "credits"
    meter = UsageMeter()
    meter.record("blockscout", "account.txlist", units=20)
    row = next(s for s in meter.summary()["sources"] if s["id"] == "blockscout")
    assert row["used"] == 20 and row["quota"] == 100_000
