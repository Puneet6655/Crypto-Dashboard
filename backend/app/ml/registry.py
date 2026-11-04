"""The model registry: saved models with their features, version, threshold and benchmark metrics.

training/model_registry.py writes backend/models/registry.json, one <id>.joblib per model and
scoring_rows.parquet (the dataset rows the API can score). This module reads them; nothing here
trains. Models load on first use and stay in memory.

Every score is an OFFLINE BENCHMARK: the models were trained on Elliptic++ wallets of 2016-2017
with hop features built from BigQuery snapshots. Live lookups cannot build hop features yet, so
only addresses in scoring_rows.parquet can be scored.
"""
from __future__ import annotations

import json
import threading
from functools import cached_property
from pathlib import Path


OFFLINE_NOTE = ("Offline benchmark: trained on Elliptic++ wallets (2016-2017). Scores work only for addresses "
                "in the benchmark dataset, because live lookups cannot build the hop features yet.")


class ModelsUnavailable(Exception):
    """No registry.json yet (models are gitignored and built by training/model_registry.py), or the
    ML packages are not installed (pip install -r requirements-ml.txt)."""


def _ml_packages():
    """pandas and joblib, imported on first use so the API still starts without requirements-ml.txt."""
    try:
        import joblib
        import pandas as pd
    except ImportError as exc:
        raise ModelsUnavailable(f"ML packages missing ({exc.name}); run: pip install -r requirements-ml.txt") from exc
    return joblib, pd


class ModelRegistry:
    def __init__(self, folder: Path):
        self.folder = folder
        self._models: dict[str, object] = {}
        self._lock = threading.Lock()

    @cached_property
    def data(self) -> dict:
        path = self.folder / "registry.json"
        if not path.exists():
            raise ModelsUnavailable(
                f"No model registry at {path}. Build it with: cd backend; "
                "..\\.venv\\Scripts\\python.exe -m training.model_registry")
        return json.loads(path.read_text(encoding="utf-8"))

    @property
    def available(self) -> bool:
        try:
            self.data
            return True
        except ModelsUnavailable:
            return False

    def entries(self) -> list[dict]:
        return self.data["models"]

    def entry(self, model_id: str) -> dict:
        for e in self.entries():
            if e["id"] == model_id:
                return e
        alert = self.data.get("alert_threshold")
        if alert and alert["model_id"] == model_id:
            return {"id": model_id, "name": "XGBoost (tuned), v2+exp+hops, all labels (alert model)",
                    "family": "xgboost", "feature_set": "v2+exp+hops", "file": alert["file"],
                    "version": alert["version"], "features": alert["features"],
                    "threshold": alert["recent_threshold"], "benchmark": "offline"}
        raise KeyError(model_id)

    @cached_property
    def known_labels(self):
        """Known illicit/licit address sets for live exposure features (app/ml/known_labels.py)."""
        _ml_packages()
        from app.ml.known_labels import load

        return load(self.folder)

    def live_id(self, family: str) -> str:
        """The live-compatible model of a family (feature set v2+exp: no hop features)."""
        for e in self.entries():
            if e["family"] == family and e.get("live_compatible") and e["feature_set"] == "v2+exp":
                return e["id"]
        raise KeyError(f"no live-compatible {family} model")

    def default_id(self, family: str, feature_set: str = "v2+exp+hops") -> str:
        for e in self.entries():
            if e["family"] == family and e["feature_set"] == feature_set:
                return e["id"]
        raise KeyError(f"{family} / {feature_set}")

    def model(self, model_id: str):
        with self._lock:
            if model_id not in self._models:
                joblib, _ = _ml_packages()
                self._models[model_id] = joblib.load(self.folder / self.entry(model_id)["file"])
            return self._models[model_id]

    @cached_property
    def rows(self):
        _, pd = _ml_packages()
        path = self.folder / "scoring_rows.parquet"
        if not path.exists():
            raise ModelsUnavailable(f"No scoring rows at {path}; run training.model_registry")
        df = pd.read_parquet(path)
        df["address"] = df["address"].astype(str)
        return df.set_index("address", drop=False).sort_index()

    def rows_for(self, address: str):
        """Every dataset row (one per Elliptic++ time step) of this address, oldest first."""
        rows = self.rows
        if address not in rows.index:
            return rows.iloc[0:0]
        found = rows.loc[[address]]
        return found.sort_values("step")

    def examples(self, per_kind: int = 3) -> list[dict]:
        """A few dataset addresses to try: illicit, licit and Ransomwhere-listed rows."""
        rows = self.rows.reset_index(drop=True)
        picks = []
        for kind, mask in (("illicit (Elliptic++)", rows.label == 1), ("licit (Elliptic++)", rows.label == 0),
                           ("ransomware (Ransomwhere)", rows.external_label == "ransomware")):
            sample = rows[mask].sample(min(per_kind, int(mask.sum())), random_state=1)
            picks += [{"address": r.address, "step": int(r.step), "kind": kind} for r in sample.itertuples()]
        return picks

    def comparison(self) -> list[dict]:
        """The benchmark table for the comparison panel (no feature lists)."""
        return [{k: v for k, v in e.items() if k != "features"} | {"feature_count": len(e["features"])}
                for e in self.entries()]


_registry: ModelRegistry | None = None


def get_model_registry() -> ModelRegistry:
    """Process-wide registry (a FastAPI dependency)."""
    global _registry
    if _registry is None:
        from app.config import get_settings

        _registry = ModelRegistry(get_settings().models_dir)
    return _registry
