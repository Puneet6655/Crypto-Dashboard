"""Elliptic++ time steps as real Bitcoin block ranges, plus two years of continuation steps.

Elliptic++ gives only a time-step number (1 to 49), no dates. Its wallet file does give real
block heights, and they show the pattern exactly: time step t is a short snapshot (6 to 16
blocks, under three hours) starting at block 391,200 + 2016 * (t - 1). 2016 blocks is one
difficulty period, about two weeks. derive_snapshot_ends() re-checks this from the CSV.

Steps 50 to 101 continue the same schedule for 104 weeks after the dataset ends, so data
from the "two years after" period is sampled the same way. Each continuation snapshot is
9 blocks long, the median Elliptic++ snapshot length. Only steps 1 to 49 have labels.
"""
from pathlib import Path

import pandas as pd

BASE_BLOCK = 391_200  # first block of time step 1
STEP_BLOCKS = 2016  # blocks between the starts of consecutive time steps
LAST_ELLIPTIC_STEP = 49
LAST_STEP = 101  # 52 more steps (about two years) after Elliptic++ ends
POST_SNAPSHOT_OFFSET = 8  # continuation snapshot = start .. start + 8 (9 blocks)

# Last block of each Elliptic++ snapshot, as an offset from the step's first block.
# From wallets_features.csv: the highest first/last_block_appeared_in inside each step.
ELLIPTIC_SNAPSHOT_END_OFFSET = {
    1: 12, 2: 8, 3: 10, 4: 10, 5: 11, 6: 7, 7: 14, 8: 12, 9: 14, 10: 9,
    11: 7, 12: 6, 13: 9, 14: 8, 15: 9, 16: 7, 17: 14, 18: 6, 19: 8, 20: 11,
    21: 10, 22: 11, 23: 12, 24: 15, 25: 7, 26: 7, 27: 5, 28: 6, 29: 7, 30: 7,
    31: 8, 32: 10, 33: 7, 34: 6, 35: 9, 36: 10, 37: 6, 38: 8, 39: 6, 40: 13,
    41: 6, 42: 12, 43: 8, 44: 9, 45: 7, 46: 7, 47: 8, 48: 8, 49: 7,
}


def step_start(step: int) -> int:
    return BASE_BLOCK + STEP_BLOCKS * (step - 1)


def steps_frame() -> pd.DataFrame:
    """One row per step: step, period ('elliptic' or 'post'), snap_start, snap_end."""
    rows = []
    for step in range(1, LAST_STEP + 1):
        start = step_start(step)
        offset = ELLIPTIC_SNAPSHOT_END_OFFSET.get(step, POST_SNAPSHOT_OFFSET)
        rows.append({
            "step": step,
            "period": "elliptic" if step <= LAST_ELLIPTIC_STEP else "post",
            "snap_start": start,
            "snap_end": start + offset,
        })
    return pd.DataFrame(rows)


def attach_dates(steps: pd.DataFrame, block_times: pd.DataFrame) -> pd.DataFrame:
    """Add snap_start_time and snap_end_time (UTC) from a block_number/block_timestamp table."""
    times = block_times.set_index("block_number")["block_timestamp"]
    out = steps.copy()
    out["snap_start_time"] = out["snap_start"].map(times)
    out["snap_end_time"] = out["snap_end"].map(times)
    return out


def derive_snapshot_ends(wallets_csv: Path) -> dict[int, int]:
    """Recompute ELLIPTIC_SNAPSHOT_END_OFFSET from the Elliptic++ wallet file (slow: 600 MB)."""
    cols = ["Time step", "first_block_appeared_in", "last_block_appeared_in"]
    w = pd.read_csv(wallets_csv, usecols=cols)
    w.columns = ["step", "first", "last"]
    start = BASE_BLOCK + STEP_BLOCKS * (w["step"] - 1)
    offsets = []
    for col in ("first", "last"):
        inside = (w[col] >= start) & (w[col] < start + STEP_BLOCKS)
        offsets.append(pd.DataFrame({"step": w.loc[inside, "step"], "offset": (w[col] - start)[inside]}))
    found = pd.concat(offsets).groupby("step")["offset"].max().astype(int)
    return found.to_dict()


if __name__ == "__main__":
    derived = derive_snapshot_ends(Path("data/raw/elliptic_pp/wallets_features.csv"))
    print("matches the table in this file:", derived == ELLIPTIC_SNAPSHOT_END_OFFSET)
