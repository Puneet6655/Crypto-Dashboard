"""Do batch features from the BigQuery recent store match live mempool.space features?

Run from backend/ after training.score_recent (about 15 mempool.space requests per address):
    ..\\.venv\\Scripts\\python.exe -m training.check_recent_parity --n 8

Picks random scored addresses with at most --max-txs transactions and no activity after the store tip
(otherwise the live window would be anchored later), recomputes the wallet, address-type and
exposure features live with the same functions, and reports every feature that differs.
Writes data/processed/recent_scores/parity.json.
"""
import argparse
import asyncio
import json

import numpy as np
import pandas as pd

from app.config import get_settings
from app.features.wallet_window import (
    address_type_features, compute_wallet_features, exposure_features, fetch_window_transactions,
    live_network_context, window_counterparties,
)
from app.ml.known_labels import load as load_known
from app.providers.registry import get_bitcoin_provider
from training.improvement_check import V2_EXP
from training.score_recent import MODELS_DIR, OUT


async def check(n: int, max_txs: int) -> dict:
    feats = pd.read_parquet(OUT / "features.parquet")
    summary = json.loads((OUT / "summary.json").read_text())
    tip = summary["store"]["tip"]
    known = load_known(MODELS_DIR)
    provider = get_bitcoin_provider(get_settings())
    pool = feats[feats.in_random & (feats.total_txs <= max_txs)].sample(frac=1, random_state=1)
    results, skipped = [], 0
    for row in pool.itertuples():
        if len(results) >= n:
            break
        txs = await fetch_window_transactions(provider, row.address)
        if not txs or max(t.block_height for t in txs) > tip:
            skipped += 1  # active again after the store tip: live window differs by design
            continue
        live = compute_wallet_features(row.address, txs, observed_at_block=tip)
        live.update(address_type_features(row.address))
        payers, payees = window_counterparties(row.address, txs, observed_at_block=tip)
        live.update(exposure_features(payers, payees, known.illicit, known.licit))
        live.update(await live_network_context(provider, live["anchor_block"], live["fees_mean"]))
        diffs = {f: (float(getattr(row, f)), float(live[f])) for f in V2_EXP
                 if not np.isclose(float(getattr(row, f)), float(live[f]), rtol=1e-6, atol=1e-9)}
        results.append({"address": row.address, "total_txs": int(row.total_txs), "differences": diffs})
        print(row.address, int(row.total_txs), "OK" if not diffs else diffs, flush=True)
    out = {"checked": len(results), "identical": sum(not r["differences"] for r in results),
           "skipped_active_after_tip": skipped, "features_compared": len(V2_EXP), "results": results}
    (OUT / "parity.json").write_text(json.dumps(out, indent=2))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n", type=int, default=8)
    parser.add_argument("--max-txs", type=int, default=40)
    args = parser.parse_args()
    out = asyncio.run(check(args.n, args.max_txs))
    print(f"{out['identical']}/{out['checked']} identical on {out['features_compared']} features "
          f"({out['skipped_active_after_tip']} skipped: active after the store tip)")


if __name__ == "__main__":
    main()
