"""Score any mainnet Bitcoin address live with a live-compatible model (feature set v2+exp).

Features come from the address's own history as the provider (mempool.space, or the backup) shows
it now, with the same rules as the training rows (app/features/wallet_window.py): the newest 100
confirmed transactions in the 4,032 blocks (about four weeks) ending at its newest transaction,
plus the network context of the 144 blocks before that, plus exposure to known-label addresses
(app/ml/known_labels.py). Hop features cannot be built live, so the hop models are not used.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from app.features.wallet_window import (
    MAX_TXS, WINDOW_BLOCKS, address_type_features, compute_wallet_features, exposure_features,
    fetch_window_transactions, live_network_context, window_counterparties,
)
from app.ml.registry import ModelRegistry, ModelsUnavailable

LIVE_NOTE = ("Live score: a pattern match against Elliptic++ labels of 2016-2017, computed from the address's "
             f"last ~4 weeks of activity ({WINDOW_BLOCKS:,} blocks ending at its newest transaction, newest "
             f"{MAX_TXS} transactions). Not proof of a crime.")


class NoActivity(Exception):
    """The address has no confirmed transaction, so there is nothing to score."""


class _CountingProvider:
    """Passes calls through, counting them and keeping the first provenance seen."""

    def __init__(self, inner):
        self.inner = inner
        self.name = getattr(inner, "name", "provider")
        self.calls = 0
        self.first_provenance = None

    async def get_address_transactions(self, address, after_txid=None):
        self.calls += 1
        page = await self.inner.get_address_transactions(address, after_txid)
        if page and self.first_provenance is None:
            self.first_provenance = page[0].provenance
        return page

    async def get_block_stats(self, height):
        self.calls += 1
        return await self.inner.get_block_stats(height)


async def live_features(provider, address: str, illicit, licit) -> dict:
    """Wallet + address-type + exposure + network features for an address now, with request facts."""
    counting = _CountingProvider(provider)
    started = time.perf_counter()
    txs = await fetch_window_transactions(counting, address)
    features = compute_wallet_features(address, txs)
    if features["anchor_block"] is None:
        raise NoActivity(f"{address} has no confirmed transactions, so there is nothing to score.")
    features.update(address_type_features(address))
    payers, payees = window_counterparties(address, txs)
    features.update(exposure_features(payers, payees, illicit, licit))
    tx_seconds = time.perf_counter() - started
    network_error = None
    try:
        features.update(await live_network_context(counting, features["anchor_block"], features["fees_mean"]))
    except Exception as exc:  # noqa: BLE001 - the backup provider has no block summaries; score without it
        network_error = f"{type(exc).__name__}: {exc}"[:200]
        features.update(net_tx_count_144=0.0, net_avg_fee_btc_144=0.0, fee_vs_network_mean=0.0)
    anchor = features["anchor_block"]
    newest = max(txs, key=lambda t: t.block_height)
    return {
        "features": features,
        "anchor_block": int(anchor),
        "anchor_time": newest.block_time.isoformat(),
        "transactions_in_window": int(features["total_txs"]),
        "window_truncated": bool(features["window_truncated"]),
        "counterparties": {"payers": len(payers), "payees": len(payees)},
        "known_label_counterparties": {
            "illicit": sorted((payers | payees) & illicit)[:20], "licit_count": len((payers | payees) & licit)},
        "network_context_error": network_error,
        "requests": counting.calls,
        "seconds": {"transactions": round(tx_seconds, 2), "total": round(time.perf_counter() - started, 2)},
        "provider": counting.first_provenance.provider if counting.first_provenance else counting.name,
        "provenance": counting.first_provenance.model_dump(mode="json") if counting.first_provenance else None,
    }


def dataset_context(registry: ModelRegistry, address: str, entry: dict, model) -> dict | None:
    """If the address is also in the offline benchmark rows: its label, step and the same model's score there."""
    try:
        rows = registry.rows_for(address)
    except ModelsUnavailable:
        return None
    if rows.empty:
        return None
    from app.ml.scoring import label_text

    row = rows.iloc[[-1]]
    missing = [f for f in entry["features"] if f not in row.columns]
    dataset_score = None if missing else round(float(model.predict_proba(row[entry["features"]])[:, 1][0]), 6)
    return {"label": label_text(row.iloc[0]), "step": int(row.iloc[0].step),
            "steps_available": [int(s) for s in rows.step], "score": dataset_score}


async def score_live(provider, address: str, family: str = "xgboost", registry: ModelRegistry | None = None,
                     model_id: str | None = None) -> dict:
    """Live score and SHAP explanation for one address. Raises ModelsUnavailable, KeyError or NoActivity."""
    import pandas as pd

    from app.ml.explain import explain
    from app.ml.registry import get_model_registry

    registry = registry or get_model_registry()
    model_id = model_id or registry.live_id(family)
    entry = registry.entry(model_id)
    if not entry.get("live_compatible"):
        raise KeyError(f"{model_id} needs hop features, which live lookups cannot build")
    labels = registry.known_labels
    live = await live_features(provider, address, labels.illicit, labels.licit)
    features = live.pop("features")
    missing = [f for f in entry["features"] if f not in features]
    if missing:
        raise RuntimeError(f"live features are missing {missing[:5]}")
    X = pd.DataFrame([[float(features[f]) for f in entry["features"]]], columns=entry["features"])
    model = registry.model(model_id)
    probability = float(model.predict_proba(X)[:, 1][0])
    return {
        "benchmark": "live",
        "note": LIVE_NOTE,
        "address": address,
        "model": {k: entry[k] for k in ("id", "name", "family", "feature_set", "version")},
        "score": round(probability, 6),
        "threshold": entry["threshold"],
        "threshold_rule": entry.get("threshold_rule"),
        "recent_2026_threshold": entry.get("recent_2026_threshold"),
        "flagged": probability >= entry["threshold"],
        "test_metrics": entry.get("metrics"),
        "explanation": explain(model, entry["family"], X),
        "live": live,
        "known_labels": {**labels.counts, "source": labels.source},
        "dataset": dataset_context(registry, address, entry, model),
        "registry_generated_utc": registry.data["generated_utc"],
        "scored_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
