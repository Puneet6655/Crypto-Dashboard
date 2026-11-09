"""Exposure-to-known-label features for the training table, using only labels known at each step.

The BigQuery pass returns, for every (address, step), the fingerprints (FARM_FINGERPRINT) of its
payers and payees. Here they are matched against known-label addresses:
  illicit = Elliptic++ class 1, Ransomwhere, OFAC;  licit = Elliptic++ class 2.
A label counts for a row only if it was known before that row's step:
  Elliptic++: from the step after the address first appears in Elliptic++;
  Ransomwhere: from the first step after its first_seen date;
  OFAC: the list has no dates, so it is used live but never in training rows.
Live lookups call app.features.wallet_window.exposure_features with address sets instead.
"""
import numpy as np
import pandas as pd
import pyarrow.compute as pc
import pyarrow.parquet as pq
from google.cloud import bigquery

from app.features.wallet_window import EXPOSURE_FEATURE_NAMES
from training import bq_common

NEVER = 10_000  # "known from" step for labels that are never usable in training


def fingerprints(client: bigquery.Client, addresses: list[str], chunk: int = 100_000) -> dict[str, int]:
    """BigQuery FARM_FINGERPRINT for each address. Reads no table, so nothing is billed."""
    out: dict[str, int] = {}
    for start in range(0, len(addresses), chunk):
        part = addresses[start:start + chunk]
        sql = "SELECT a AS address, FARM_FINGERPRINT(a) AS fp FROM UNNEST(@addresses) AS a"
        params = [bigquery.ArrayQueryParameter("addresses", "STRING", part)]
        job = bq_common.run_query(client, sql, params, label="fingerprints", max_gb=0.1)
        out.update({row["address"]: int(row["fp"]) for row in job.result()})
    return out


def label_table(elliptic_rows: pd.DataFrame, elliptic_class: pd.Series, external: pd.DataFrame | None,
                steps: pd.DataFrame) -> pd.DataFrame:
    """One row per labelled address: address, cls (1 illicit, 0 licit), known_after_step."""
    first_step = elliptic_rows.groupby("address")["step"].min()
    ell = elliptic_class[elliptic_class.isin([1, 2])]
    frames = [pd.DataFrame({"address": ell.index, "cls": (ell == 1).astype(int).to_numpy(),
                            "known_after_step": first_step.reindex(ell.index).fillna(NEVER).astype(int).to_numpy()})]
    if external is not None and len(external):
        ends = pd.to_datetime(steps.sort_values("step")["snap_end_time"], utc=True).to_numpy()
        seen = pd.to_datetime(external["first_seen"], errors="coerce", utc=True)
        # Number of steps that ended before first_seen = the last step at which it was not yet known.
        known = np.where(seen.isna() | (external["source"] != "ransomwhere"), NEVER,
                         np.searchsorted(ends, seen.to_numpy(), side="left"))
        frames.append(pd.DataFrame({"address": external["address"], "cls": 1, "known_after_step": known}))
    table = pd.concat(frames, ignore_index=True)
    # An address on both lists counts as illicit, known from the earliest illicit source.
    table = table.sort_values(["address", "cls", "known_after_step"], ascending=[True, False, True])
    return table.drop_duplicates("address")


def exposure_frame(features_path, labels: pd.DataFrame, fp_of: dict[str, int], batch_rows: int = 300_000) -> pd.DataFrame:
    """EXPOSURE_FEATURE_NAMES for every row of the features file, in file order."""
    labels = labels.assign(fp=labels["address"].map(fp_of)).dropna(subset=["fp"])
    labels = labels.sort_values("fp")
    label_fp = labels["fp"].to_numpy(np.int64)
    label_cls = labels["cls"].to_numpy(np.int8)
    label_known = labels["known_after_step"].to_numpy(np.int32)

    parts = []
    for batch in pq.ParquetFile(features_path).iter_batches(columns=["step", "payer_fps", "payee_fps"], batch_size=batch_rows):
        step = batch.column("step").to_numpy()
        n = len(step)
        out = {}
        for side, column in (("payer", "payer_fps"), ("payee", "payee_fps")):
            lists = batch.column(column)
            flat = pc.list_flatten(lists).to_numpy(zero_copy_only=False).astype(np.int64)
            parent = pc.list_parent_indices(lists).to_numpy(zero_copy_only=False)
            sizes = pc.fill_null(pc.list_value_length(lists), 0).to_numpy(zero_copy_only=False)
            pos = np.clip(np.searchsorted(label_fp, flat), 0, len(label_fp) - 1)
            usable = (label_fp[pos] == flat) & (label_known[pos] < step[parent])
            illicit = np.bincount(parent[usable & (label_cls[pos] == 1)], minlength=n)
            licit = np.bincount(parent[usable & (label_cls[pos] == 0)], minlength=n)
            safe = np.where(sizes > 0, sizes, 1)
            out[f"illicit_{side}s"] = illicit.astype(float)
            out[f"licit_{side}s"] = licit.astype(float)
            out[f"illicit_{side}_share"] = np.where(sizes > 0, illicit / safe, 0.0)
            out[f"licit_{side}_share"] = np.where(sizes > 0, licit / safe, 0.0)
        parts.append(pd.DataFrame(out)[list(EXPOSURE_FEATURE_NAMES)])
    return pd.concat(parts, ignore_index=True)
