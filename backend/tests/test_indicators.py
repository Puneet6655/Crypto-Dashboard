"""Offline tests for the indicator snapshots (download checks, matching) and investigation screening."""
import json

import httpx
from fastapi.testclient import TestClient

from app.intelligence import snapshots
from app.intelligence.index import IndicatorIndex, get_indicator_index
from app.intelligence.sources import SOURCES
from app.main import app
from app.providers.bitcoin_mempool import MempoolSpaceProvider
from app.providers.registry import get_bitcoin_provider
from tests.fixtures.mempool_synthetic import SYNTHETIC_TXID
from tests.test_bitcoin_lookup import fake_mempool

LISTED = "1BoatSLRHtKNngkdXEeobR76b53LETtpyT"  # an output address of the synthetic transaction
RANSOMWHERE_EXPORT = {"result": [{
    "address": "bc1qransomexampleaddressxxxxxxxxxxxxxxxxxx", "blockchain": "bitcoin", "family": "Locky",
    "createdAt": "2021-07-08T06:43:08.978Z", "updatedAt": "2021-07-08T06:43:08.978Z", "balance": 1,
    "transactions": [{"hash": "c" * 64, "time": 1587839053, "amount": 1}],
}]}


def lists_api(xbt: list[str], ransomwhere: dict = RANSOMWHERE_EXPORT) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "api.github.com" in url:
            return httpx.Response(200, json=[{"commit": {"committer": {"date": "2026-10-29T07:13:51Z"}}}])
        if url.endswith("sanctioned_addresses_XBT.json"):
            return httpx.Response(200, json=xbt)
        if url.endswith("sanctioned_addresses_ETH.json"):
            return httpx.Response(200, json=["0x8589427373D6D84E98730D7795D8f6f8731FDA16"])
        if url.endswith("sanctioned_addresses_SOL.json"):
            return httpx.Response(200, json=["FuCC7GoYwt5TsNTjWL23Xx9UKCvC18chjMEFPL3vJDCC"])
        if url == SOURCES["ransomwhere"].url:
            return httpx.Response(200, json=ransomwhere)
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def download(folder, xbt, **kwargs):
    with httpx.Client(transport=lists_api(xbt)) as client:
        return {r.source_id: r for r in snapshots.refresh(folder, client=client, **kwargs)}


def test_refresh_saves_snapshot_with_provenance_and_labels(tmp_path):
    results = download(tmp_path, [LISTED, "3Fake"])
    assert {r.status for r in results.values()} == {"updated"}
    meta = snapshots.read_meta(tmp_path, "ofac_xbt")
    assert meta["record_count"] == 2 and meta["list_updated_at"] == "2026-10-29T07:13:51Z"
    assert len(meta["sha256"]) == 64
    labels = (tmp_path / snapshots.LABELS_FILE).read_text(encoding="utf-8").splitlines()
    assert labels[0].startswith("address,source,label,family")
    assert any(line.startswith(f"{LISTED},ofac_xbt,sanctioned") for line in labels)
    assert any(",ransomwhere,ransomware,Locky,2020-04-25" in line for line in labels)
    # Downloading the same files again is reported as unchanged.
    assert download(tmp_path, [LISTED, "3Fake"])["ofac_xbt"].status == "unchanged"


def test_bad_or_shrunken_download_keeps_the_old_snapshot(tmp_path):
    download(tmp_path, [f"1Addr{i}" for i in range(10)])
    with httpx.Client(transport=lists_api({"not": "a list"})) as client:
        assert snapshots.refresh(tmp_path, ["ofac_xbt"], client=client)[0].status == "rejected"
    assert download(tmp_path, ["1Addr0"])["ofac_xbt"].status == "rejected"
    assert snapshots.read_meta(tmp_path, "ofac_xbt")["record_count"] == 10
    assert download(tmp_path, ["1Addr0"], force=True)["ofac_xbt"].status == "updated"


def test_matching_rules(tmp_path):
    download(tmp_path, [LISTED])
    index = IndicatorIndex(tmp_path)
    assert [h.source_id for h in index.lookup(LISTED)] == ["ofac_xbt"]
    assert index.lookup(LISTED.lower()) == []  # Base58 is case-sensitive
    assert index.lookup("BC1QRANSOMEXAMPLEADDRESSXXXXXXXXXXXXXXXXXX")[0].family == "Locky"  # bech32 is not
    assert index.lookup("0x8589427373d6d84e98730d7795d8f6f8731fda16")[0].source_id == "ofac_eth"
    assert index.lookup("1NotListed") == []


def test_investigation_screens_graph_addresses(tmp_path):
    download(tmp_path, [LISTED])
    index = IndicatorIndex(tmp_path)
    app.dependency_overrides[get_bitcoin_provider] = lambda: MempoolSpaceProvider(
        "https://mempool.test/api", "mainnet", 5, transport=httpx.MockTransport(fake_mempool))
    app.dependency_overrides[get_indicator_index] = lambda: index
    try:
        body = TestClient(app).post("/api/investigations", json={
            "mode": "crypto_investigation", "input_type": "bitcoin_txid", "value": SYNTHETIC_TXID,
            "method": "live_lookup",
        }).json()
    finally:
        app.dependency_overrides.pop(get_indicator_index, None)

    rules = {i["rule"]: i for i in body["indicators"]}
    assert rules["ofac_address_in_transaction"]["matched"] is True
    assert rules["ransomwhere_address_in_transaction"]["matched"] is False
    assert "does not mean an address is safe" in rules["ransomwhere_address_in_transaction"]["reason"]
    match = [f for f in body["findings"] if f["kind"] == "rule_match"][0]
    assert match["entity_ids"] == [f"bitcoin:mainnet:address:{LISTED}"]
    evidence = {e["id"]: e for e in body["evidence"]}
    assert evidence[match["evidence_ids"][0]]["source"] == SOURCES["ofac_xbt"].title
    node = next(n for n in body["graph"]["nodes"] if n["id"] == match["entity_ids"][0])
    assert node["details"]["listed_in"] == [SOURCES["ofac_xbt"].title]
    assert body["uncertainty"]["data_sources"] == "mempool.space"


def test_missing_snapshot_means_rule_not_run(tmp_path):
    app.dependency_overrides[get_bitcoin_provider] = lambda: MempoolSpaceProvider(
        "https://mempool.test/api", "mainnet", 5, transport=httpx.MockTransport(fake_mempool))
    app.dependency_overrides[get_indicator_index] = lambda: IndicatorIndex(tmp_path)
    try:
        body = TestClient(app).post("/api/investigations", json={
            "mode": "crypto_investigation", "input_type": "bitcoin_txid", "value": SYNTHETIC_TXID,
            "method": "live_lookup",
        })
    finally:
        app.dependency_overrides.pop(get_indicator_index, None)
    assert body.status_code == 200, body.text
    assert all(i["matched"] is None and i["reason"].startswith("Not run") for i in body.json()["indicators"])


def test_check_route(tmp_path):
    download(tmp_path, [LISTED])
    app.dependency_overrides[get_indicator_index] = lambda: IndicatorIndex(tmp_path)
    try:
        client = TestClient(app)
        hit = client.get(f"/api/indicators/check/{LISTED}").json()
        miss = client.get("/api/indicators/check/1NotListedAddressxxxxxxxxx").json()
        status = client.get("/api/indicators/status").json()
    finally:
        app.dependency_overrides.pop(get_indicator_index, None)
    assert hit["matched"] and hit["hits"][0]["source_id"] == "ofac_xbt"
    assert not miss["matched"] and len(miss["checked"]) == 4
    assert {s["source_id"] for s in status if s["loaded"]} == set(SOURCES)
    json.dumps(status)
