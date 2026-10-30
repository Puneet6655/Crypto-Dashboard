"""Recompute sample dataset rows from live mempool.space data and compare.

Proves that a feature row built from BigQuery equals what the live provider gives for the
same address and observation block. Picks addresses with short histories (few requests),
fetches every transaction through the app's MempoolSpaceProvider, and calls the same
compute_wallet_features() with observed_at_block = the row's snapshot end. Network context
is checked too. Read-only GET requests, about one per second.

    python -m training.check_live_parity --rows 25
"""
import argparse
import asyncio
import math

import pandas as pd

from app.config import get_settings
from app.features.wallet_window import (
    FEATURE_NAMES,
    NETWORK_FEATURE_NAMES,
    compute_wallet_features,
    live_network_context,
    tx_record_from_summary,
)
from app.providers.base import ProviderError
from app.providers.bitcoin_mempool import MempoolSpaceProvider
from training.build_training_dataset import DATASET

MAX_HISTORY = 40  # skip addresses with longer histories: too many requests


async def call(fn, *args, **kwargs):
    """One provider call, waiting and retrying on rate limits."""
    for attempt in range(4):
        try:
            result = await fn(*args, **kwargs)
            await asyncio.sleep(1.0)
            return result
        except ProviderError as exc:
            if exc.status_code != 429 or attempt == 3:
                raise
            await asyncio.sleep(30)


async def full_history(provider, address: str) -> list:
    records, after = [], None
    while True:
        page = await call(provider.get_address_transactions, address, after_txid=after)
        confirmed = [r for r in (tx_record_from_summary(tx) for tx in page) if r is not None]
        records += confirmed
        if len(confirmed) < 25:
            return records
        after = confirmed[-1].txid


def close(a, b) -> bool:
    return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-12)


async def main_async(rows: int, seed: int) -> None:
    settings = get_settings()
    provider = MempoolSpaceProvider(settings.btc_provider_base_url, settings.btc_network, 20)
    df = pd.read_parquet(DATASET)
    pool = df[(df.window_truncated == 0) & (df.total_txs <= 20)]
    # Spread over sources (labelled, same-era, post) and over time steps.
    candidates = pool.groupby("source").sample(n=rows * 3, random_state=seed)

    checked, bad_rows, network_checked = 0, 0, 0
    for row in candidates.sample(frac=1, random_state=seed).itertuples():
        if checked >= rows:
            break
        summary = await call(provider.get_address_summary, row.address)
        if summary.confirmed.tx_count > MAX_HISTORY:
            continue
        txs = await full_history(provider, row.address)
        live = compute_wallet_features(row.address, txs, observed_at_block=int(row.snap_end))
        diffs = [n for n in FEATURE_NAMES + ("anchor_block",) if not close(live[n] or 0, getattr(row, n))]
        if network_checked < 3:
            live.update(await live_network_context(provider, int(row.anchor_block), live["fees_mean"]))
            diffs += [n for n in NETWORK_FEATURE_NAMES if not close(live[n], getattr(row, n))]
            network_checked += 1
        checked += 1
        status = "ok" if not diffs else f"DIFF {diffs[:6]}"
        print(f"{checked:3d} {row.source:12s} step {row.step:3d} {row.address[:14]:14s} txs {int(row.total_txs):3d}  {status}")
        if diffs:
            bad_rows += 1
            for n in diffs[:6]:
                print(f"      {n}: dataset {getattr(row, n)} live {live[n]}")
    print(f"\n{checked} rows recomputed from mempool.space ({network_checked} with network context), {bad_rows} differ")
    if bad_rows:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rows", type=int, default=25)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    asyncio.run(main_async(args.rows, args.seed))


if __name__ == "__main__":
    main()
