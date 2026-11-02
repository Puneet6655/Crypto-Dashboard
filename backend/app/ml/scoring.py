"""Score a dataset address with a registered model and explain the score (Milestone 12).

Used by POST /api/models/score and by the "Random Forest" and "XGBoost" investigation methods.
Everything here is an offline benchmark: see app/ml/registry.py.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.intelligence.index import IndicatorIndex
from app.intelligence.screening import screen_bitcoin
from app.ml.registry import OFFLINE_NOTE, ModelRegistry, ModelsUnavailable, get_model_registry
from app.schemas.investigation import (
    Evidence, Finding, Graph, GraphLimits, GraphNode, InvestigationInput, InvestigationRequest,
    InvestigationResult, Scorecard,
)
from app.validation import is_bitcoin_address, normalize_address

LABELS = {1: "illicit", 0: "licit"}


class NotInDataset(Exception):
    """The address has no row in the benchmark dataset, so no model can score it."""


def label_text(row) -> str:
    import pandas as pd

    if not pd.isna(row.label):
        return f"Elliptic++ label: {LABELS[int(row.label)]}"
    if isinstance(row.external_label, str):
        return f"listed as {row.external_label} (Ransomwhere/OFAC), no Elliptic++ label"
    return "no label"


def score(address: str, model_id: str | None = None, step: int | None = None, family: str = "xgboost",
          registry: ModelRegistry | None = None) -> dict:
    """Score one dataset row and explain it. Raises ModelsUnavailable, KeyError (model) or NotInDataset."""
    registry = registry or get_model_registry()
    model_id = model_id or registry.default_id(family)
    entry = registry.entry(model_id)
    address = normalize_address(address)
    rows = registry.rows_for(address)
    if rows.empty:
        raise NotInDataset(
            f"{address} is not in the offline benchmark dataset. Models can only score dataset addresses "
            "(Elliptic++ test steps 35-49 and Ransomwhere/OFAC-listed rows), because live lookups cannot "
            "build the hop features yet.")
    if step is not None:
        rows = rows[rows.step == step]
        if rows.empty:
            raise NotInDataset(f"{address} has no dataset row at step {step}.")
    row = rows.iloc[[-1]]
    r = row.iloc[0]
    X = row[entry["features"]]
    model = registry.model(model_id)
    probability = float(model.predict_proba(X)[:, 1][0])
    from app.ml.explain import explain  # numpy/pandas/shap: only loaded when a model is used

    explanation = explain(model, entry["family"], X)
    return {
        "benchmark": "offline",
        "note": OFFLINE_NOTE,
        "address": address,
        "step": int(r.step),
        "steps_available": [int(s) for s in rows_for_all(registry, address)],
        "label": label_text(r),
        "model": {k: entry[k] for k in ("id", "name", "family", "feature_set", "version")},
        "score": round(probability, 6),
        "threshold": entry["threshold"],
        "flagged": probability >= entry["threshold"],
        "test_metrics": entry.get("metrics"),
        "explanation": explanation,
        "registry_generated_utc": registry.data["generated_utc"],
    }


def rows_for_all(registry: ModelRegistry, address: str) -> list[int]:
    return list(registry.rows_for(address).step)


def investigate_with_model(request: InvestigationRequest, indicators: IndicatorIndex,
                           registry: ModelRegistry | None = None) -> InvestigationResult:
    """The 'Random Forest' and 'XGBoost' methods: score a dataset address, as an investigation result."""
    from app.services.investigation_service import UnsupportedInvestigation, new_investigation_id

    if request.input_type != "bitcoin_address":
        raise UnsupportedInvestigation("The offline benchmark models score Bitcoin addresses from the dataset only.")
    if not is_bitcoin_address(request.value):
        raise UnsupportedInvestigation("That does not look like a mainnet Bitcoin address.")
    registry = registry or get_model_registry()
    try:
        result = score(request.value, family=request.method, registry=registry)
    except ModelsUnavailable as exc:
        raise UnsupportedInvestigation(f"No trained models on this server. {exc}")
    except NotInDataset as exc:
        raise UnsupportedInvestigation(str(exc))

    address = result["address"]
    node_id = f"bitcoin:mainnet:address:{address}"
    model, expl = result["model"], result["explanation"]
    created = datetime.fromisoformat(result["registry_generated_utc"])
    verdict = "at or above" if result["flagged"] else "below"
    top = ", ".join(f"{f['feature']} ({'+' if f['contribution'] > 0 else ''}{f['contribution']:.3f})"
                    for f in expl["features"][:3])
    findings = [
        Finding(id="f1", kind="model_output", entity_ids=[node_id], evidence_ids=["e1", "e2"],
                title=(f"{model['name']}: score {result['score']:.3f}, {verdict} its threshold "
                       f"{result['threshold']:.2f} (offline benchmark, Elliptic++ step {result['step']})")),
        Finding(id="f2", kind="model_output", entity_ids=[node_id], evidence_ids=["e3"],
                title=f"Biggest SHAP contributions ({expl['units']}): {top}"),
        Finding(id="f3", kind="observation", entity_ids=[node_id], evidence_ids=["e1"],
                title=f"Dataset row: {result['label']}; rows at steps {', '.join(map(str, result['steps_available']))}"),
    ]
    metrics = result["test_metrics"] or {}
    evidence = [
        Evidence(id="e1", source="Benchmark dataset row (BigQuery features, Elliptic++ labels)",
                 source_url="backend/models/scoring_rows.parquet", retrieved_at=created,
                 method_version="training/model_registry.py", excerpt=f"address {address}, step {result['step']}"),
        Evidence(id="e2", source=f"Model registry: {model['id']}", source_url="backend/models/registry.json",
                 retrieved_at=created, method_version=model["version"],
                 excerpt=(f"test steps 35-49: PR-AUC {metrics.get('PR-AUC', 0):.3f}, F1 {metrics.get('F1', 0):.3f}, "
                          f"threshold {result['threshold']:.2f} chosen on steps 31-34")),
        Evidence(id="e3", source=expl["method"], source_url="https://shap.readthedocs.io/",
                 retrieved_at=datetime.now(timezone.utc), method_version=model["version"],
                 excerpt=(f"base value {expl['base_value']:.4f} + contributions = output {expl['output']:.4f} "
                          f"({expl['units']})")),
    ]
    screening = screen_bitcoin(indicators, "mainnet", address, [address], first_finding=len(findings) + 1,
                               first_evidence=len(evidence) + 1)
    node = GraphNode(id=node_id, type="address", label=f"{address[:6]}...{address[-6:]}",
                     details={"model_score": round(result["score"], 4), "threshold": result["threshold"],
                              "dataset_step": result["step"], "dataset_label": result["label"]})
    if screening.flagged_nodes:
        node = node.model_copy(update={"details": {**node.details, "listed_in": next(iter(screening.flagged_nodes.values()))}})
    now = datetime.now(timezone.utc)
    return InvestigationResult(
        investigation_id=new_investigation_id(now), created_at=now,
        input=InvestigationInput(type="bitcoin_address", value=address),
        mode=request.mode, method=request.method, data_mode="recorded",
        findings=findings + screening.findings, indicators=screening.indicators,
        graph=Graph(nodes=[node], edges=[], truncated=False, limits=GraphLimits(max_nodes=1)),
        evidence=evidence + screening.evidence,
        source_timestamps={"model registry": created, **screening.source_timestamps},
        limitations=[
            OFFLINE_NOTE,
            "A score is a pattern match against 2016-2017 Elliptic++ labels, not proof of a crime",
            "SHAP explains what drove the model's score, not why the address is or is not illicit",
        ],
        uncertainty={"benchmark": "offline", "score": f"{result['score']:.4f}", "flagged": bool(result["flagged"])},
        scorecard=Scorecard(configured=False),
        explanation=result,
    )
