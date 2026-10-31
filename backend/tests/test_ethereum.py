"""Offline tests for the Etherscan adapter and Ethereum investigations (recorded-style responses)."""
import asyncio
import logging

import httpx
from fastapi.testclient import TestClient

from app.intelligence import snapshots
from app.intelligence.index import IndicatorIndex, get_indicator_index
from app.main import app
from app.providers.base import ProviderError
from app.providers.ethereum_etherscan import EtherscanProvider
from app.providers.registry import get_ethereum_provider
from tests.test_indicators import lists_api

KEY = "TESTKEY123"
ADDR = "0x" + "a" * 40
OTHER = "0x" + "b" * 40
SANCTIONED = "0x8589427373d6d84e98730d7795d8f6f8731fda16"  # in the fake OFAC ETH list of test_indicators
TOKEN = "0x" + "c" * 40
TX = "0x" + "1" * 64
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def ok(result):
    return httpx.Response(200, json={"status": "1", "message": "OK", "result": result})


def rpc(result):
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})


def fake_etherscan(state: dict | None = None):
    state = state if state is not None else {}

    def handler(request: httpx.Request) -> httpx.Response:
        q = dict(request.url.params)
        state.setdefault("keys", []).append(q.get("apikey"))
        if state.get("rate_limit_once") and not state.get("limited"):
            state["limited"] = True
            return httpx.Response(200, json={"status": "0", "message": "NOTOK",
                                             "result": "Max calls per sec rate limit reached (3/sec)"})
        action = q.get("action")
        if action == "balance":
            return ok("1500000000000000000")
        if action == "eth_getTransactionCount":
            return rpc("0x2a")
        if action == "txlist":
            return ok([{"blockNumber": "100", "timeStamp": "1700000000", "hash": TX, "from": ADDR, "to": SANCTIONED,
                        "value": "250000000000000000", "gasUsed": "21000", "gasPrice": "1000000000", "isError": "0",
                        "txreceipt_status": "1", "contractAddress": "", "methodId": "0x", "functionName": ""}])
        if action == "tokentx":
            return ok([{"blockNumber": "99", "timeStamp": "1699990000", "hash": "0x" + "2" * 64, "from": OTHER,
                        "to": ADDR, "contractAddress": TOKEN, "value": "2500000", "tokenSymbol": "USDC",
                        "tokenDecimal": "6"}])
        if action in ("txlistinternal", "tokennfttx", "token1155tx"):
            return httpx.Response(200, json={"status": "0", "message": "No transactions found", "result": []})
        if action == "eth_getTransactionByHash":
            return rpc({"hash": TX, "blockNumber": "0x64", "from": ADDR, "to": TOKEN, "value": "0x0",
                        "gasPrice": "0x3b9aca00", "input": "0xa9059cbb" + "0" * 128})
        if action == "eth_getTransactionReceipt":
            return rpc({"status": "0x1", "gasUsed": "0x5208", "effectiveGasPrice": "0x3b9aca00", "contractAddress": None,
                        "logs": [{"address": TOKEN, "data": hex(1_500_000),
                                  "topics": [TRANSFER, "0x" + "0" * 24 + ADDR[2:], "0x" + "0" * 24 + OTHER[2:]]}]})
        if action == "eth_getBlockByNumber":
            return rpc({"timestamp": hex(1700000000)})
        if action == "eth_blockNumber":
            return rpc("0x1312d00")
        return httpx.Response(200, json={"status": "0", "message": "NOTOK", "result": "Error! Invalid address format"})
    return httpx.MockTransport(handler)


def provider(state=None) -> EtherscanProvider:
    return EtherscanProvider(KEY, "https://etherscan.test/v2/api", 1, 5, transport=fake_etherscan(state), min_interval=0)


def test_address_summary_and_transactions_keep_the_key_out_of_provenance():
    p, state = None, {}
    p = provider(state)
    summary = asyncio.run(p.get_address_summary(ADDR.upper().replace("0X", "0x")))
    assert summary.balance_wei == "1500000000000000000" and summary.sent_tx_count == 42
    assert summary.address == ADDR and summary.entity_id == f"ethereum:mainnet:address:{ADDR}"
    assert KEY not in summary.provenance.source_url and "chainid=1" in summary.provenance.source_url
    assert state["keys"] == [KEY, KEY]  # the key is sent, just never stored

    txs = asyncio.run(p.get_address_transactions(ADDR))
    assert txs[0].fee_wei == str(21000 * 10**9) and txs[0].to_address == SANCTIONED and txs[0].status == "success"
    [token] = asyncio.run(p.get_token_transfers(ADDR))
    assert (token.token_symbol, token.value_raw, token.to_address) == ("USDC", "2500000", ADDR)
    assert asyncio.run(p.get_internal_transfers(ADDR)) == []  # "No transactions found" is an empty list
    assert "offset=1000" in p.list_url("txlist", ADDR) and KEY not in p.list_url("txlist", ADDR)


def test_transaction_decodes_receipt_and_erc20_transfer():
    tx = asyncio.run(provider().get_transaction(TX))
    assert tx.status == "success" and tx.block_number == 100 and tx.block_time.year == 2023
    assert tx.method == "0xa9059cbb" and tx.fee_wei == str(21000 * 10**9)
    [t] = tx.token_transfers
    assert (t.token_contract, t.from_address, t.to_address, t.value_raw) == (TOKEN, ADDR, OTHER, "1500000")


def test_per_second_rate_limit_is_retried_once():
    state = {"rate_limit_once": True}
    assert asyncio.run(provider(state).get_address_summary(ADDR)).sent_tx_count == 42


def test_invalid_request_is_a_404_not_a_provider_failure():
    p = provider()
    try:
        asyncio.run(p._call({"module": "account", "action": "unknown"}))
        raise AssertionError("expected ProviderError")
    except ProviderError as exc:
        assert exc.status_code == 404 and "Invalid address format" in exc.message
    assert p.failures == 0


def test_httpx_log_lines_hide_the_key(caplog):
    with caplog.at_level(logging.INFO, logger="httpx"):
        asyncio.run(provider().health())
    assert caplog.records and all(KEY not in r.getMessage() for r in caplog.records)
    assert any("apikey=REDACTED" in r.getMessage() for r in caplog.records)


def test_ethereum_investigation_screens_against_ofac(tmp_path):
    with httpx.Client(transport=lists_api(["1Fake"])) as client:
        snapshots.refresh(tmp_path, client=client)
    app.dependency_overrides[get_ethereum_provider] = lambda: provider()
    app.dependency_overrides[get_indicator_index] = lambda: IndicatorIndex(tmp_path)
    try:
        client = TestClient(app)
        body = client.post("/api/investigations", json={
            "mode": "crypto_investigation", "input_type": "ethereum_address", "value": ADDR, "method": "live_lookup",
        }).json()
        tx_body = client.post("/api/investigations", json={
            "mode": "crypto_investigation", "input_type": "ethereum_txid", "value": TX, "method": "live_lookup",
        }).json()
    finally:
        app.dependency_overrides.pop(get_ethereum_provider, None)
        app.dependency_overrides.pop(get_indicator_index, None)

    rules = {i["rule"]: i for i in body["indicators"]}
    assert rules["ofac_exact_match"]["matched"] is False
    assert rules["ofac_counterparty_match"]["matched"] is True
    titles = [f["title"] for f in body["findings"]]
    assert titles[0] == "Balance 1.5 ETH; 42 transactions sent"
    assert titles[1].startswith("Fetched 1 transactions (full history), 1 token transfers (full history)")
    assert "Most transferred tokens: USDC (1)" in titles
    assert any(t.startswith("ETH in fetched transactions: received 0 (0 transactions), sent 0.25 (1)") for t in titles)
    assert body["uncertainty"]["history_complete"] is True and body["uncertainty"]["counterparties"] == 2
    assert {e["label"] for e in body["graph"]["edges"]} >= {"received 2.5 USDC", "sent 0.25 ETH"}
    flagged = next(n for n in body["graph"]["nodes"] if n["id"] == f"ethereum:mainnet:address:{SANCTIONED}")
    assert flagged["details"]["listed_in"] == ["OFAC sanctioned Ethereum-format addresses"]
    assert all(KEY not in e["source_url"] for e in body["evidence"])
    assert body["uncertainty"]["data_sources"] == "etherscan.io"

    assert tx_body["input"] == {"type": "ethereum_txid", "value": TX}
    assert "1 ERC-20 transfers" in tx_body["findings"][1]["title"]
    labels = {e["label"] for e in tx_body["graph"]["edges"]}
    assert f"sent 1,500,000 units tokens of {TOKEN[:6]}...{TOKEN[-6:]}" in labels


def test_without_a_key_ethereum_is_reported_as_off():
    app.dependency_overrides[get_ethereum_provider] = lambda: None
    try:
        client = TestClient(app)
        response = client.post("/api/investigations", json={
            "mode": "crypto_investigation", "input_type": "ethereum_address", "value": ADDR, "method": "live_lookup"})
        status = client.get("/api/ethereum/providers").json()
    finally:
        app.dependency_overrides.pop(get_ethereum_provider, None)
    assert response.status_code == 422 and "ETHERSCAN_API_KEY" in response.json()["detail"]
    assert status["configured"] is False
