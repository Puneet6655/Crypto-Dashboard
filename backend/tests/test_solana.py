"""Offline tests for Solana lookups (Alchemy Solana JSON-RPC, jsonParsed transactions) and investigations."""
import asyncio
import json
import logging

import httpx
from fastapi.testclient import TestClient

from app.intelligence import snapshots
from app.intelligence.index import IndicatorIndex, get_indicator_index
from app.main import app
from app.providers.registry import get_solana_provider
from app.providers.solana_alchemy import SolanaProvider
from app.validation import is_solana_address, is_solana_signature
from tests.test_indicators import lists_api

KEY = "ALCHEMYTESTKEY99"
ME = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"
SANCTIONED = "FuCC7GoYwt5TsNTjWL23Xx9UKCvC18chjMEFPL3vJDCC"  # in the fake OFAC SOL list
FRIEND = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
MY_USDC = "3emsAVdmGKERbHjmGfQ6oZ1e35dkf5iYcS6U4CPKFVaa"
FRIEND_USDC = "BQcdHdAQW1hczDbBi9hiegXAR7A98Q9jx3X3iBBBDiq4"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SIG1 = "5UfDuX7WXY18keiz9mZ6zKkY8JyNuLDFz2QycQcr7skRkgVaNmo6tgFbsePRrX5C6crvycJ2A3txSdGgjPHvc4nb"
SIG2 = "4Nd1mBQtrMJVYVfKf2PJy9NZUZdTAsp7D4xWLs4gDB4T3QFR9Q6tUYXqd1bh6ggN4e1QeF3tfZuL7HZTYBSaH1Nr"


def tx1():
    """ME sends 1.5 SOL to the sanctioned address and 2.5 USDC to FRIEND (as an inner instruction)."""
    return {
        "slot": 300000000, "blockTime": 1793400000,
        "meta": {"err": None, "fee": 5000,
                 "preBalances": [10_000_000_000, 0, 2_039_280, 2_039_280, 1],
                 "postBalances": [8_499_995_000, 1_500_000_000, 2_039_280, 2_039_280, 1],
                 "preTokenBalances": [
                     {"accountIndex": 2, "mint": USDC, "owner": ME, "uiTokenAmount": {"amount": "10000000", "decimals": 6}},
                     {"accountIndex": 3, "mint": USDC, "owner": FRIEND, "uiTokenAmount": {"amount": "0", "decimals": 6}}],
                 "postTokenBalances": [
                     {"accountIndex": 2, "mint": USDC, "owner": ME, "uiTokenAmount": {"amount": "7500000", "decimals": 6}},
                     {"accountIndex": 3, "mint": USDC, "owner": FRIEND, "uiTokenAmount": {"amount": "2500000", "decimals": 6}}],
                 "innerInstructions": [{"index": 1, "instructions": [
                     {"program": "spl-token", "programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                      "parsed": {"type": "transfer", "info": {"source": MY_USDC, "destination": FRIEND_USDC,
                                                              "amount": "2500000", "authority": ME}}}]}]},
        "transaction": {"signatures": [SIG1], "message": {
            "accountKeys": [{"pubkey": ME, "signer": True, "writable": True},
                            {"pubkey": SANCTIONED, "signer": False, "writable": True},
                            {"pubkey": MY_USDC, "signer": False, "writable": True},
                            {"pubkey": FRIEND_USDC, "signer": False, "writable": True},
                            {"pubkey": "11111111111111111111111111111111", "signer": False, "writable": False}],
            "instructions": [
                {"program": "system", "programId": "11111111111111111111111111111111",
                 "parsed": {"type": "transfer", "info": {"source": ME, "destination": SANCTIONED, "lamports": 1_500_000_000}}},
                {"programId": "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4", "accounts": [], "data": "x"}]}},
    }


def tx2():
    """FRIEND sends 0.25 SOL to ME; it failed."""
    return {"slot": 299999999, "blockTime": 1793300000,
            "meta": {"err": {"InstructionError": [0, "Custom"]}, "fee": 5000, "preBalances": [], "postBalances": []},
            "transaction": {"message": {"accountKeys": [{"pubkey": FRIEND, "signer": True}, {"pubkey": ME, "signer": False}],
                                        "instructions": [{"program": "system", "parsed": {"type": "transfer", "info": {
                                            "source": FRIEND, "destination": ME, "lamports": 250_000_000}}}]}}}


def fake_rpc(calls: list):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith(f"/v2/{KEY}") and request.url.host == "solana-mainnet.g.alchemy.com"
        body = json.loads(request.content)
        method, params = body["method"], body["params"]
        calls.append(method)
        result = {
            "getBalance": {"context": {"slot": 1}, "value": 8_499_995_000},
            "getAccountInfo": {"context": {"slot": 1}, "value": {"owner": "11111111111111111111111111111111",
                                                                 "executable": False, "lamports": 8_499_995_000}},
            "getSignaturesForAddress": [
                {"signature": SIG1, "slot": 300000000, "blockTime": 1793400000, "err": None},
                {"signature": SIG2, "slot": 299999999, "blockTime": 1793300000, "err": {"x": 1}}],
            "getSlot": 300000001,
        }.get(method)
        if method == "getTransaction":
            result = {SIG1: tx1(), SIG2: tx2()}.get(params[0])
        if method == "getTokenAccountsByOwner":
            result = {"value": [{"pubkey": MY_USDC, "account": {"data": {"parsed": {"info": {
                "mint": USDC, "tokenAmount": {"amount": "7500000", "decimals": 6}}}}}}]
                if params[1]["programId"].startswith("Tokenkeg") else []}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})
    return httpx.MockTransport(handler)


def provider(calls=None):
    return SolanaProvider(KEY, 5, transport=fake_rpc(calls if calls is not None else []), cu_per_second=1e9)


def test_validation():
    assert is_solana_address(ME) and not is_solana_address("0x" + "a" * 40)
    assert not is_solana_address("bc1qm34lsc65zpw79lxes69zkqmk6ee3ewf0j77s3h")
    assert is_solana_signature(SIG1) and not is_solana_signature(ME)


def test_parse_sol_and_inner_token_transfers_with_owners():
    tx = asyncio.run(provider().get_transaction(SIG1))
    assert tx.status == "success" and tx.fee_lamports == 5000 and tx.signers == [ME]
    sol_t, token_t = tx.transfers
    assert (sol_t.kind, sol_t.from_address, sol_t.to_address, sol_t.amount_raw) == ("sol", ME, SANCTIONED, "1500000000")
    # Token accounts are mapped to the wallets that own them.
    assert (token_t.from_address, token_t.to_address, token_t.mint, token_t.decimals) == (ME, FRIEND, USDC, 6)
    assert tx.balance_changes[SANCTIONED] == 1_500_000_000
    assert "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4" in tx.programs
    assert KEY not in tx.provenance.source_url


def test_holdings_list_token_balances():
    h = asyncio.run(provider().get_holdings(ME))
    assert h.balance_lamports == "8499995000"
    assert [(t.mint, t.amount) for t in h.tokens] == [(USDC, "7.5")]


def test_key_is_hidden_in_log_lines(caplog):
    with caplog.at_level(logging.INFO, logger="httpx"):
        asyncio.run(provider().health())
    assert caplog.records and all(KEY not in r.getMessage() for r in caplog.records)


def _investigate(tmp_path, value, input_type):
    with httpx.Client(transport=lists_api(["1Fake"])) as client:
        snapshots.refresh(tmp_path, client=client)
    p = provider()
    app.dependency_overrides[get_solana_provider] = lambda: p
    app.dependency_overrides[get_indicator_index] = lambda: IndicatorIndex(tmp_path)
    try:
        return TestClient(app).post("/api/investigations", json={
            "mode": "crypto_investigation", "input_type": input_type, "value": value, "method": "live_lookup"})
    finally:
        app.dependency_overrides.pop(get_solana_provider, None)
        app.dependency_overrides.pop(get_indicator_index, None)


def test_address_investigation_flags_sanctioned_counterparty(tmp_path):
    body = _investigate(tmp_path, ME, "solana_address").json()
    ids = {n["id"] for n in body["graph"]["nodes"]}
    assert f"solana:mainnet:address:{ME}" in ids and f"solana:mainnet:tx:{SIG1}" in ids
    flagged = [n for n in body["graph"]["nodes"] if n["id"] == f"solana:mainnet:address:{SANCTIONED}"]
    assert flagged and flagged[0]["details"]["listed_in"]
    rules = {i["rule"]: i["matched"] for i in body["indicators"]}
    assert rules == {"ofac_exact_match": False, "ofac_counterparty_match": True}
    titles = " | ".join(f["title"] for f in body["findings"])
    assert "Balance 8.499995 SOL" in titles and "2 transactions (full history); 1 failed" in titles
    assert "sent 1.5 SOL" in titles and "USDC (1)" in titles
    assert body["uncertainty"]["data_sources"] == "alchemy.com"


def test_transaction_investigation_and_bad_input(tmp_path):
    body = _investigate(tmp_path, SIG1, "solana_txid").json()
    assert body["findings"][0]["title"].startswith("Transaction succeeded at slot 300,000,000")
    assert any(i["rule"] == "ofac_address_in_transaction" and i["matched"] for i in body["indicators"])
    labels = {e["label"] for e in body["graph"]["edges"]}
    assert "received 2.5 USDC" in labels
    assert _investigate(tmp_path, "0x" + "a" * 40, "solana_address").status_code == 422


def test_solana_off_without_alchemy():
    from dataclasses import replace

    from app.config import get_settings

    settings = replace(get_settings(), alchemy_api_key=KEY, alchemy_enabled=False)
    assert get_solana_provider(settings) is None
    assert get_solana_provider(replace(settings, alchemy_enabled=True)) is not None
    app.dependency_overrides[get_solana_provider] = lambda: None
    try:
        r = TestClient(app).get(f"/api/solana/address/{ME}")
    finally:
        app.dependency_overrides.pop(get_solana_provider, None)
    assert r.status_code == 503 and "ALCHEMY_ENABLED" in r.json()["detail"]
