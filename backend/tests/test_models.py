"""Offline tests for Milestones 8 and 12: graph measures, the model registry, scoring and SHAP.

Small models are trained here on synthetic rows and saved to a temporary registry, so the tests never
need the real (gitignored) models or dataset.
"""
import json
import math
import pickle

import joblib
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, StandardScaler
from xgboost import XGBClassifier

from app.intelligence import snapshots
from app.intelligence.sources import SOURCES
from app.main import app
from app.ml.explain import explain
from app.ml.registry import ModelRegistry, get_model_registry
from app.ml.scoring import score
from app.ml.transforms import signed_log
from tests.test_bitcoin_lookup import use_fake_provider, use_fake_store
from tests.fixtures.mempool_synthetic import SYNTHETIC_ADDRESS, SYNTHETIC_TXID

client = TestClient(app)
FEATURES = ["f_amount", "f_count", "f_hops"]
ILLICIT = "1BoatSLRHtKNngkdXEeobR76b53LETtpyT"
LICIT = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
RANSOM = "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy"


@pytest.fixture(autouse=True)
def _restore_overrides():
    """Do not leave the fake provider and store behind for later test files."""
    from app.providers.registry import get_bitcoin_provider
    from app.store import get_store

    yield
    app.dependency_overrides.pop(get_bitcoin_provider, None)
    app.dependency_overrides.pop(get_store, None)


def synthetic(n=400, seed=0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    X = pd.DataFrame({"f_amount": rng.lognormal(0, 2, n), "f_count": rng.poisson(5, n).astype(float),
                      "f_hops": rng.integers(1, 7, n).astype(float)})
    y = ((X.f_hops <= 2) & (X.f_amount > 1)).astype(int)
    return X.assign(label=y)


def train_all(df):
    X, y = df[FEATURES], df.label
    lr = Pipeline([("impute", SimpleImputer(strategy="median")), ("log", FunctionTransformer(signed_log)),
                   ("scale", StandardScaler()), ("model", LogisticRegression(max_iter=1000))]).fit(X, y)
    rf = RandomForestClassifier(n_estimators=20, max_depth=4, random_state=0).fit(X, y)
    xgb = XGBClassifier(n_estimators=30, max_depth=3, device="cpu").fit(X, y)
    return {"logistic_regression": lr, "random_forest": rf, "xgboost": xgb}


@pytest.fixture(scope="module")
def registry(tmp_path_factory):
    folder = tmp_path_factory.mktemp("models")
    df = synthetic()
    models = train_all(df)
    entries = []
    for family, model in models.items():
        joblib.dump(model, folder / f"{family}.joblib")
        entries.append({"id": f"{family}__v2_exp_hops", "name": f"{family} test", "family": family,
                        "feature_set": "v2+exp+hops", "version": "test", "file": f"{family}.joblib",
                        "features": FEATURES, "threshold": 0.5, "metrics": {"PR-AUC": 0.9, "F1": 0.8},
                        "seeds": {"n": 1, "pr_auc_mean": 0.9, "pr_auc_std": 0.0}, "beats_baseline": None,
                        "benchmark": "offline"})
    (folder / "registry.json").write_text(json.dumps({
        "generated_utc": "2026-11-02T00:00:00+00:00", "benchmark": "test", "positive_class": "illicit",
        "test_rows": len(df), "test_illicit": int(df.label.sum()), "models": entries}))
    rows = pd.DataFrame([
        {"address": ILLICIT, "step": 40, "source": "elliptic_pp", "label": 1, "external_label": None,
         "f_amount": 5.0, "f_count": 3.0, "f_hops": 1.0},
        {"address": ILLICIT, "step": 42, "source": "elliptic_pp", "label": 1, "external_label": None,
         "f_amount": 8.0, "f_count": 4.0, "f_hops": 1.0},
        {"address": RANSOM, "step": 60, "source": "bq_post", "label": None, "external_label": "ransomware",
         "f_amount": 3.0, "f_count": 2.0, "f_hops": 2.0},
        {"address": LICIT, "step": 41, "source": "elliptic_pp", "label": 0, "external_label": None,
         "f_amount": 0.2, "f_count": 9.0, "f_hops": 6.0},
    ])
    rows["label"] = rows.label.astype("Int64")
    rows.to_parquet(folder / "scoring_rows.parquet", index=False)
    reg = ModelRegistry(folder)
    app.dependency_overrides[get_model_registry] = lambda: reg
    yield reg
    app.dependency_overrides.pop(get_model_registry, None)


@pytest.mark.parametrize("family", ["logistic_regression", "random_forest", "xgboost"])
def test_shap_contributions_sum_to_the_model_output(family):
    df = synthetic()
    model = train_all(df)[family]
    for i in range(5):
        row = df[FEATURES].iloc[[i]]
        e = explain(model, family, row, top=2)
        total = e["base_value"] + sum(f["contribution"] for f in e["features"]) + e["other_features"]["contribution"]
        assert math.isclose(total, e["output"], abs_tol=1e-4)
        assert math.isclose(e["probability"], float(model.predict_proba(row)[:, 1][0]), abs_tol=1e-4)
        assert len(e["features"]) == 2 and e["other_features"]["count"] == 1


def test_registry_lists_models_with_offline_label(registry):
    body = client.get("/api/models").json()
    assert body["available"] is True
    assert "Offline benchmark" in body["note"] and "live lookups cannot build the hop features" in body["note"]
    assert {m["family"] for m in body["models"]} == {"logistic_regression", "random_forest", "xgboost"}
    assert all("features" not in m and m["feature_count"] == 3 for m in body["models"])


def test_score_dataset_address_uses_latest_step_and_explains(registry):
    body = client.post("/api/models/score", json={"address": ILLICIT}).json()
    assert body["benchmark"] == "offline"
    assert body["step"] == 42 and body["steps_available"] == [40, 42]
    assert body["model"]["family"] == "xgboost"
    assert 0 <= body["score"] <= 1 and body["flagged"] == (body["score"] >= body["threshold"])
    assert body["explanation"]["features"][0]["feature"] in FEATURES
    picked = client.post("/api/models/score", json={"address": ILLICIT, "step": 40,
                                                     "model_id": "random_forest__v2_exp_hops"}).json()
    assert picked["step"] == 40 and picked["explanation"]["units"] == "probability"


def test_listed_row_without_elliptic_label_can_be_scored(registry):
    body = client.post("/api/models/score", json={"address": RANSOM}).json()
    assert body["step"] == 60 and "ransomware" in body["label"] and "no Elliptic++ label" in body["label"]


def test_score_rejects_addresses_outside_the_dataset(registry):
    response = client.post("/api/models/score", json={"address": SYNTHETIC_ADDRESS})
    assert response.status_code == 404
    assert "not in the offline benchmark dataset" in response.json()["detail"]
    assert client.post("/api/models/score", json={"address": ILLICIT, "model_id": "nope"}).status_code == 404


def test_registry_reports_missing_models(tmp_path):
    app.dependency_overrides[get_model_registry] = lambda: ModelRegistry(tmp_path)
    try:
        body = client.get("/api/models").json()
        assert body["available"] is False and "training.model_registry" in body["detail"]
        assert client.post("/api/models/score", json={"address": ILLICIT}).status_code == 503
    finally:
        app.dependency_overrides.pop(get_model_registry, None)


def test_model_investigation_needs_a_live_compatible_model(registry, monkeypatch):
    """This registry has hop models only, so the live method refuses (tests/test_live_scoring.py covers scoring)."""
    import app.ml.scoring as scoring

    monkeypatch.setattr(scoring, "get_model_registry", lambda: registry)
    use_fake_provider()
    use_fake_store()
    response = client.post("/api/investigations", json={
        "mode": "crypto_investigation", "input_type": "bitcoin_address", "value": LICIT, "method": "random_forest",
    })
    assert response.status_code == 422 and "No live-compatible model" in response.json()["detail"]


def test_graph_measures_add_networkx_values_to_every_node():
    use_fake_provider()
    use_fake_store()
    body = client.post("/api/investigations", json={
        "mode": "crypto_investigation", "input_type": "bitcoin_txid", "value": SYNTHETIC_TXID, "method": "graph_measures",
    }).json()
    assert body["method"] == "graph_measures"
    nodes = body["graph"]["nodes"]
    assert all({"betweenness", "pagerank", "degree"} <= set(n["details"]) for n in nodes)
    tx = next(n for n in nodes if n["type"] == "transaction")
    # Every input and output hangs off the one transaction, so it is the bridge of the graph.
    assert tx["details"]["betweenness"] == max(n["details"]["betweenness"] for n in nodes)
    assert tx["details"]["degree"] == len(nodes) - 1
    assert math.isclose(sum(n["details"]["pagerank"] for n in nodes), 1, abs_tol=0.01)
    titles = [f["title"] for f in body["findings"]]
    assert any(t.startswith("Bridges (highest betweenness)") for t in titles)
    assert any(e["method_version"].startswith("graph_measures_v1") for e in body["evidence"])
    assert any("Graph measures describe only the graph shown" in l for l in body["limitations"])


def test_parsed_snapshot_cache_is_used_and_follows_the_file(tmp_path):
    source = SOURCES["ofac_xbt"]
    snapshots.raw_path(tmp_path, source.id, source.extension).write_text(json.dumps([ILLICIT, LICIT]))
    first = snapshots.load_records(tmp_path, source)
    cache = snapshots.cache_path(tmp_path, source.id)
    assert cache.exists()
    # The second load reads the cache: plant a marker in it and see it come back.
    key, _ = pickle.loads(cache.read_bytes())
    cache.write_bytes(pickle.dumps((key, ["from cache"])))
    assert snapshots.load_records(tmp_path, source) == ["from cache"]
    assert len(first) == 2
    # A changed snapshot file is parsed again, not served from the old cache.
    snapshots.raw_path(tmp_path, source.id, source.extension).write_text(json.dumps([LICIT]))
    assert [r.address for r in snapshots.load_records(tmp_path, source)] == [LICIT]
    # A broken cache file is ignored and rebuilt.
    snapshots.cache_path(tmp_path, source.id).write_bytes(b"not a pickle")
    assert [r.address for r in snapshots.load_records(tmp_path, source)] == [LICIT]
