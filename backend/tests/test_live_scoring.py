"""Offline tests for live model scoring in investigations (methods random_forest / xgboost).

A fake mempool.space serves a small history; small models are trained on synthetic rows with the
real v2+exp feature names, so no real model, dataset or network is needed.
"""
import json

import httpx
import joblib
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sklearn.ensemble import RandomForestClassifier
from xgboost import XGBClassifier

from app.features.wallet_window import (
    ADDRESS_FEATURE_NAMES, EXPOSURE_FEATURE_NAMES, FEATURE_NAMES, compute_wallet_features, tx_record_from_summary,
)
from app.main import app
from app.ml.known_labels import FILE_NAME
from app.ml.registry import ModelRegistry, get_model_registry
from app.providers.bitcoin_mempool import MempoolSpaceProvider
from app.providers.registry import get_bitcoin_provider
from tests.test_bitcoin_lookup import use_fake_store

client = TestClient(app)
V2_EXP = list(FEATURE_NAMES) + list(ADDRESS_FEATURE_NAMES) + ["fee_vs_network_mean"] + list(EXPOSURE_FEATURE_NAMES)
SUBJECT = "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh"
BAD_PAYER = "1BoatSLRHtKNngkdXEeobR76b53LETtpyT"  # on the fake illicit list
GOOD_PAYEE = "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy"  # on the fake licit list
OTHER = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
QUIET = "1A1zP1eP5QGefi2DMPTfTL5SNLv7DivfNa"  # no confirmed transactions
TIP = 800_000


def tx(i: int, vin: list, vout: list, height: int) -> dict:
    return {"txid": f"{i:064x}", "fee": 500 + i, "weight": 800,
            "vin": [{"prevout": {"scriptpubkey_address": a, "value": v}} for a, v in vin],
            "vout": [{"scriptpubkey_address": a, "value": v} for a, v in vout],
            "status": {"confirmed": True, "block_height": height, "block_time": 1_790_000_000 + height * 600}}


HISTORY = [  # newest first, as mempool.space returns it
    tx(3, [(SUBJECT, 90_000)], [(GOOD_PAYEE, 50_000), (SUBJECT, 39_000)], TIP),
    tx(2, [(BAD_PAYER, 200_000)], [(SUBJECT, 100_000), (OTHER, 99_000)], TIP - 10),
    tx(1, [(OTHER, 30_000)], [(SUBJECT, 29_000)], TIP - 3000),
]


def fake_mempool(request: httpx.Request, fail_blocks: bool = False) -> httpx.Response:
    path = request.url.path
    if path == f"/api/address/{SUBJECT}":
        return httpx.Response(200, json={"address": SUBJECT, "chain_stats": {
            "funded_txo_count": 2, "funded_txo_sum": 129_000, "spent_txo_count": 1, "spent_txo_sum": 90_000,
            "tx_count": 3}, "mempool_stats": {"funded_txo_count": 0, "funded_txo_sum": 0, "spent_txo_count": 0,
                                              "spent_txo_sum": 0, "tx_count": 0}})
    if path == f"/api/address/{SUBJECT}/txs":
        return httpx.Response(200, json=HISTORY)
    if path.startswith(f"/api/address/{SUBJECT}/txs/chain/"):
        return httpx.Response(200, json=[])
    if path == f"/api/address/{QUIET}/txs":
        return httpx.Response(200, json=[])
    if path.startswith("/api/v1/blocks/"):
        if fail_blocks:
            return httpx.Response(503, text="unavailable")
        top = int(path.rsplit("/", 1)[1])
        return httpx.Response(200, json=[{"height": h, "tx_count": 11, "extras": {"totalFees": 100_000}}
                                         for h in range(top, top - 15, -1)])
    return httpx.Response(404, text="not found")


def use_provider(fail_blocks: bool = False):
    app.dependency_overrides[get_bitcoin_provider] = lambda: MempoolSpaceProvider(
        base_url="https://mempool.test/api", network="mainnet", timeout_seconds=5,
        transport=httpx.MockTransport(lambda r: fake_mempool(r, fail_blocks)))


@pytest.fixture(autouse=True)
def _restore_overrides():
    from app.store import get_store

    yield
    for dep in (get_bitcoin_provider, get_store, get_model_registry):
        app.dependency_overrides.pop(dep, None)


@pytest.fixture(scope="module")
def registry(tmp_path_factory):
    folder = tmp_path_factory.mktemp("live_models")
    rng = np.random.default_rng(0)
    X = pd.DataFrame(rng.lognormal(0, 1, (600, len(V2_EXP))), columns=V2_EXP)
    X["illicit_payers"] = rng.integers(0, 3, 600).astype(float)
    y = (X.illicit_payers > 0).astype(int)
    models = {"xgboost": XGBClassifier(n_estimators=20, max_depth=3, device="cpu").fit(X, y),
              "random_forest": RandomForestClassifier(n_estimators=20, max_depth=4, random_state=0).fit(X, y)}
    entries = []
    for family, model in models.items():
        joblib.dump(model, folder / f"{family}.joblib")
        entries.append({"id": f"{family}__v2_exp", "name": f"{family} live test", "family": family,
                        "feature_set": "v2+exp", "version": "test", "file": f"{family}.joblib", "features": V2_EXP,
                        "threshold": 0.5, "threshold_rule": "test", "metrics": {"PR-AUC": 0.9, "F1": 0.8},
                        "live_compatible": True, "benchmark": "offline"})
    entries[0]["recent_2026_threshold"] = 0.9
    (folder / "registry.json").write_text(json.dumps({
        "generated_utc": "2026-11-02T00:00:00+00:00", "benchmark": "test", "positive_class": "illicit",
        "test_rows": 600, "test_illicit": int(y.sum()), "models": entries}))
    pd.DataFrame({"address": [BAD_PAYER, GOOD_PAYEE], "cls": [1, 0], "source": ["ofac_xbt", "elliptic_pp"]}) \
        .astype({"cls": "int8"}).to_parquet(folder / FILE_NAME, index=False)
    row = {"address": SUBJECT, "step": 41, "source": "elliptic_pp", "label": 0, "external_label": None,
           **{f: 0.0 for f in V2_EXP}}
    pd.DataFrame([row]).astype({"label": "Int64"}).to_parquet(folder / "scoring_rows.parquet", index=False)
    return ModelRegistry(folder)


def investigate(registry, monkeypatch, value=SUBJECT, method="xgboost"):
    import app.ml.scoring as scoring

    monkeypatch.setattr(scoring, "get_model_registry", lambda: registry)
    use_fake_store()
    return client.post("/api/investigations", json={
        "mode": "crypto_investigation", "input_type": "bitcoin_address", "value": value, "method": method})


@pytest.mark.parametrize("family", ["xgboost", "random_forest"])
def test_any_address_is_scored_live_with_the_live_model(registry, monkeypatch, family):
    use_provider()
    response = investigate(registry, monkeypatch, method=family)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data_mode"] == "live" and body["method"] == family
    e = body["explanation"]
    assert e["benchmark"] == "live" and e["model"]["id"] == f"{family}__v2_exp"
    # The score is the model applied to exactly the features the shared feature code computes.
    plain = MempoolSpaceProvider("https://mempool.test/api", "mainnet", 5)
    records = [tx_record_from_summary(plain._to_tx_summary(t, "https://mempool.test/api")) for t in HISTORY]
    expected = compute_wallet_features(SUBJECT, records)
    assert e["live"]["transactions_in_window"] == expected["total_txs"] == 3
    assert e["live"]["anchor_block"] == TIP
    assert e["live"]["known_label_counterparties"] == {"illicit": [BAD_PAYER], "licit_count": 1}
    assert e["live"]["network_context_error"] is None
    assert 0 <= e["score"] <= 1 and e["flagged"] == (e["score"] >= e["threshold"])
    assert e["explanation"]["features"] and abs(e["explanation"]["probability"] - e["score"]) < 1e-4
    titles = [f["title"] for f in body["findings"]]
    assert "live score" in titles[0] and "Biggest SHAP contributions" in titles[1]
    assert "1 on known illicit lists, 1 known licit" in titles[2]
    assert any("Also in the offline benchmark dataset: Elliptic++ label: licit" in t for t in titles)
    assert any("2016-2017" in l for l in body["limitations"]) and any("~4 weeks" in l for l in body["limitations"])
    sources = [ev["source"] for ev in body["evidence"]]
    assert sources[0].startswith("mempool.space address transactions") and any("Model registry" in s for s in sources)
    if family == "xgboost":
        assert "2026 alert threshold 0.900" in titles[0]


def test_exposure_features_reach_the_model(registry, monkeypatch):
    use_provider()
    e = investigate(registry, monkeypatch).json()["explanation"]
    shown = {f["feature"]: f["value"] for f in e["explanation"]["features"]}
    assert shown.get("illicit_payers") == 1.0  # the synthetic models key on it, so it is a top feature


def test_scoring_still_works_without_block_summaries(registry, monkeypatch):
    use_provider(fail_blocks=True)
    body = investigate(registry, monkeypatch).json()
    assert body["data_mode"] == "live"
    assert body["explanation"]["live"]["network_context_error"]
    assert any("fee_vs_network_mean was set to 0" in l for l in body["limitations"])


def test_address_without_confirmed_history_is_refused(registry, monkeypatch):
    use_provider()
    response = investigate(registry, monkeypatch, value=QUIET)
    assert response.status_code == 422 and "no confirmed transactions" in response.json()["detail"]


def test_known_labels_fall_back_to_raw_files(tmp_path):
    from app.ml.known_labels import load

    raw = tmp_path / "data" / "raw"
    (raw / "elliptic_pp").mkdir(parents=True)
    (raw / "indicators").mkdir(parents=True)
    (raw / "elliptic_pp" / "wallets_classes.csv").write_text(
        f"address,class\n{BAD_PAYER},1\n{GOOD_PAYEE},2\n{OTHER},3\n{SUBJECT},2\n")
    (raw / "indicators" / "bitcoin_labels.csv").write_text(f"address,source,label\n{SUBJECT},ransomwhere,ransomware\n")
    labels = load(tmp_path / "models")
    assert labels.illicit == {BAD_PAYER, SUBJECT}  # on both lists: illicit wins
    assert labels.licit == {GOOD_PAYEE}


def test_default_live_lookup_adds_the_live_xgboost_score(registry, monkeypatch):
    import app.ml.registry as reg_module

    monkeypatch.setattr(reg_module, "get_model_registry", lambda: registry)
    use_provider()
    use_fake_store()
    monkeypatch.setattr(registry, "warm", False, raising=False)
    cold = client.post("/api/investigations", json={
        "mode": "crypto_investigation", "input_type": "bitcoin_address", "value": SUBJECT, "method": "live_lookup"}).json()
    assert cold["explanation"] is None and any("still loading" in l for l in cold["limitations"])

    from app.ml.live_scoring import warm_up

    assert warm_up(registry) is True
    body = client.post("/api/investigations", json={
        "mode": "crypto_investigation", "input_type": "bitcoin_address", "value": SUBJECT, "method": "live_lookup"}).json()
    model = [f for f in body["findings"] if f["kind"] == "model_output"]
    assert len(model) == 1 and "live score" in model[0]["title"] and "Why Flagged?" in model[0]["title"]
    assert body["explanation"]["benchmark"] == "live" and body["explanation"]["model"]["id"] == "xgboost__v2_exp"
    assert not any("No model has scored" in l for l in body["limitations"])
    ids = [e["id"] for e in body["evidence"]]
    assert len(ids) == len(set(ids)) and model[0]["evidence_ids"][0] in ids
    finding_ids = [f["id"] for f in body["findings"]]
    assert len(finding_ids) == len(set(finding_ids))
