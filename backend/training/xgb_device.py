"""Where XGBoost trains: the GPU when available, else the CPU.

Set XGB_DEVICE=cpu (or cuda, cuda:1, ...) to choose. The default "cuda" falls back to "cpu" when
this XGBoost build has no CUDA support or no GPU answers; the check runs once per process.
GPU and CPU "hist" training give slightly different models (floating-point order), so compare runs
on the same device. Predictions on NumPy/pandas input still work: XGBoost copies the data across and
may log a "mismatched devices" warning, which is harmless.
"""
import os
from functools import lru_cache

import numpy as np


@lru_cache(maxsize=1)
def xgb_device() -> str:
    wanted = os.getenv("XGB_DEVICE", "cuda").strip().lower() or "cuda"
    if not wanted.startswith("cuda"):
        return "cpu"
    try:
        import xgboost

        if not xgboost.build_info().get("USE_CUDA"):
            raise RuntimeError("this XGBoost build has no CUDA support")
        probe = xgboost.XGBClassifier(n_estimators=1, max_depth=1, tree_method="hist", device=wanted)
        probe.fit(np.array([[0.0], [1.0]]), np.array([0, 1]))
        return wanted
    except Exception as exc:  # noqa: BLE001 - any failure means: use the CPU
        print(f"XGBoost: GPU not usable ({exc}); training on the CPU", flush=True)
        return "cpu"
