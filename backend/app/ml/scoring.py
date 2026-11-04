"""Score a dataset address with a registered model and explain the score (Milestone 12).

score() is the offline benchmark used by POST /api/models/score (dataset addresses only, see
app/ml/registry.py). investigate_with_model() is the "Random Forest" and "XGBoost" investigation
methods: it scores any address live (app/ml/live_scoring.py).
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
from app.features.wallet_window import MAX_TXS, WINDOW_BLOCKS
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


async def investigate_with_model(request: InvestigationRequest, provider, indicators: IndicatorIndex,
                                 registry: ModelRegistry | None = None) -> InvestigationResult:
    """The 'Random Forest' and 'XGBoost' methods: score any mainnet address live, as an investigation result.

    Uses the live-compatible model of the family (v2+exp). If the address is also in the offline
    benchmark rows, its dataset label is shown too, but the live score is the primary result.
    """
    from app.ml.live_scoring import LIVE_NOTE, NoActivity, score_live
    from app.services.investigation_service import UnsupportedInvestigation, new_investigation_id

    if request.input_type != "bitcoin_address":
        raise UnsupportedInvestigation("The models score Bitcoin addresses only.")
    if not is_bitcoin_address(request.value):
        raise UnsupportedInvestigation("That does not look like a mainnet Bitcoin address.")
    registry = registry or get_model_registry()
    address = normalize_address(request.value)
    try:
        result = await score_live(provider, address, family=request.method, registry=registry)
    except ModelsUnavailable as exc:
        raise UnsupportedInvestigation(f"No trained models on this server. {exc}")
    except KeyError as exc:
        raise UnsupportedInvestigation(f"No live-compatible model for this method ({exc}).")
    except NoActivity as exc:
        raise UnsupportedInvestigation(str(exc))

    node_id = f"bitcoin:mainnet:address:{address}"
    model, expl, live = result["model"], result["explanation"], result["live"]
    created = datetime.fromisoformat(result["registry_generated_utc"])
    now = datetime.now(timezone.utc)
    verdict = "at or above" if result["flagged"] else "below"
    top = ", ".join(f"{f['feature']} ({'+' if f['contribution'] > 0 else ''}{f['contribution']:.3f})"
                    for f in expl["features"][:3])
    recent = result.get("recent_2026_threshold")
    recent_text = (f"; {'at or above' if result['score'] >= recent else 'below'} the 2026 alert threshold "
                   f"{recent:.3f} (flags 1% of recent active addresses)") if recent else ""
    anchor_day = live["anchor_time"][:10]
    exposure = live["known_label_counterparties"]
    findings = [
        Finding(id="f1", kind="model_output", entity_ids=[node_id], evidence_ids=["e1", "e3"],
                title=(f"{model['name']}: live score {result['score']:.3f}, {verdict} its threshold "
                       f"{result['threshold']:.2f}{recent_text}")),
        Finding(id="f2", kind="model_output", entity_ids=[node_id], evidence_ids=["e4"],
                title=f"Biggest SHAP contributions ({expl['units']}): {top}"),
        Finding(id="f3", kind="observation", entity_ids=[node_id], evidence_ids=["e1", "e2"],
                title=(f"Scored on {live['transactions_in_window']} confirmed transactions in the {WINDOW_BLOCKS:,} "
                       f"blocks ending at block {live['anchor_block']:,} ({anchor_day})"
                       + (f", capped at the newest {MAX_TXS}" if live["window_truncated"] else "")
                       + f"; {live['counterparties']['payers']} payers, {live['counterparties']['payees']} payees, "
                       f"{len(exposure['illicit'])} on known illicit lists, {exposure['licit_count']} known licit")),
    ]
    metrics = result["test_metrics"] or {}
    prov = live["provenance"] or {}
    evidence = [
        Evidence(id="e1", source=f"{live['provider']} address transactions ({live['requests']} requests)",
                 source_url=prov.get("source_url", ""),
                 retrieved_at=datetime.fromisoformat(prov["retrieved_at"]) if prov else now,
                 method_version="app/features/wallet_window.py",
                 excerpt=(f"{live['transactions_in_window']} transactions in window, anchor block "
                          f"{live['anchor_block']}, {live['seconds']['total']:.1f} s")),
        Evidence(id="e2", source="Known-label lists (Elliptic++ classes, Ransomwhere, OFAC)",
                 source_url=f"backend/models/{result['known_labels']['source']}", retrieved_at=created,
                 method_version="app/ml/known_labels.py",
                 excerpt=(f"{result['known_labels']['illicit']:,} illicit and {result['known_labels']['licit']:,} "
                          "licit addresses; exposure counts counterparties on them")),
        Evidence(id="e3", source=f"Model registry: {model['id']}", source_url="backend/models/registry.json",
                 retrieved_at=created, method_version=model["version"],
                 excerpt=(f"offline test steps 35-49: PR-AUC {metrics.get('PR-AUC', 0):.3f}, "
                          f"F1 {metrics.get('F1', 0):.3f}, threshold {result['threshold']:.2f} chosen on steps 31-34")),
        Evidence(id="e4", source=expl["method"], source_url="https://shap.readthedocs.io/",
                 retrieved_at=now, method_version=model["version"],
                 excerpt=(f"base value {expl['base_value']:.4f} + contributions = output {expl['output']:.4f} "
                          f"({expl['units']})")),
    ]
    dataset = result["dataset"]
    if dataset:
        score_text = (f"; the same model scores that 2017 row {dataset['score']:.3f}"
                      if dataset["score"] is not None else "")
        findings.append(Finding(
            id="f4", kind="observation", entity_ids=[node_id], evidence_ids=["e5"],
            title=(f"Also in the offline benchmark dataset: {dataset['label']} (rows at steps "
                   f"{', '.join(map(str, dataset['steps_available']))}){score_text}")))
        evidence.append(Evidence(
            id="e5", source="Benchmark dataset row (BigQuery features, Elliptic++ labels)",
            source_url="backend/models/scoring_rows.parquet", retrieved_at=created,
            method_version="training/model_registry.py", excerpt=f"address {address}, step {dataset['step']}"))
    screening = screen_bitcoin(indicators, "mainnet", address, [address], first_finding=len(findings) + 1,
                               first_evidence=len(evidence) + 1)
    details = {"model_score": round(result["score"], 4), "threshold": result["threshold"],
               "anchor_block": live["anchor_block"]}
    if dataset:
        details |= {"dataset_step": dataset["step"], "dataset_label": dataset["label"]}
    if screening.flagged_nodes:
        details["listed_in"] = next(iter(screening.flagged_nodes.values()))
    node = GraphNode(id=node_id, type="address", label=f"{address[:6]}...{address[-6:]}", details=details)
    limitations = [
        LIVE_NOTE,
        "Labels are from 2016-2017 (Elliptic++) plus Ransomwhere/OFAC lists; behaviour has drifted since, and "
        "there are no 2026 labels to measure live accuracy",
        "Hop (graph) features cannot be built live, so the stronger hop models are not used here",
        "SHAP explains what drove the model's score, not why the address is or is not illicit",
    ]
    if live["network_context_error"]:
        limitations.append("Network fee context was unavailable from the provider, so fee_vs_network_mean was set to 0")
    return InvestigationResult(
        investigation_id=new_investigation_id(now), created_at=now,
        input=InvestigationInput(type="bitcoin_address", value=address),
        mode=request.mode, method=request.method, data_mode="live",
        findings=findings + screening.findings, indicators=screening.indicators,
        graph=Graph(nodes=[node], edges=[], truncated=False, limits=GraphLimits(max_nodes=1)),
        evidence=evidence + screening.evidence,
        source_timestamps={live["provider"]: evidence[0].retrieved_at, "model registry": created,
                           **screening.source_timestamps},
        limitations=limitations,
        uncertainty={"benchmark": "live", "score": f"{result['score']:.4f}", "flagged": bool(result["flagged"])},
        scorecard=Scorecard(configured=False),
        explanation=result,
    )
