"""Red flags for one Bitcoin address: read its history, work out the facts, run the packs.

Used by POST /api/red-flags/bitcoin (the panel next to the model score) and by the
"authority_rules" investigation method.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.features.wallet_window import fetch_window_transactions
from app.redflags.engine import build_report
from app.redflags.facts import bitcoin_facts
from app.redflags.packs import PackRegistry, get_pack_registry

log = logging.getLogger(__name__)


def _known_illicit():
    """The model's known illicit addresses, or None when this server has no model files."""
    try:
        from app.ml.registry import get_model_registry

        registry = get_model_registry()
        if not registry.available:
            return None
        return registry.known_labels.illicit
    except Exception as exc:  # noqa: BLE001 - no labels is a normal state; the label facts stay unknown
        log.info("known labels unavailable for red flags: %s", exc)
        return None


async def bitcoin_red_flags(provider, address: str, index=None, registry: PackRegistry | None = None,
                            illicit=None, use_known_labels: bool = True) -> dict:
    registry = registry or get_pack_registry()
    txs = await fetch_window_transactions(provider, address)
    if illicit is None and use_known_labels:
        illicit = _known_illicit()
    bundle = bitcoin_facts(address, txs, index, illicit)
    return build_report(registry, "bitcoin", "mainnet", address, bundle,
                        data_source=getattr(provider, "name", "Bitcoin provider"),
                        retrieved_at=datetime.now(timezone.utc))
