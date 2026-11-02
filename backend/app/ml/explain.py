"""SHAP explanations for one prediction ("Why flagged?").

Tree models (Random Forest, XGBoost) use shap.TreeExplainer, which is exact for trees. The
logistic-regression baseline is linear, so its SHAP values are exact in closed form: weight x
(standardised feature value - its training mean, which is 0 after scaling).

Units differ by model and are reported with the values:
* XGBoost and logistic regression: log-odds (the model's raw output before the sigmoid).
* Random Forest: probability of the illicit class.
In every case base_value + sum(contributions) == output (checked in tests/test_models.py).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TOP_FEATURES = 12


def _sigmoid(x: float) -> float:
    return float(1 / (1 + np.exp(-x)))


def shap_values(model, family: str, row: pd.DataFrame) -> tuple[np.ndarray, float, float, str]:
    """(contributions per feature, base value, model output, units) for a one-row DataFrame."""
    if family == "logistic_regression":
        transformed = row
        for name, step in model.steps[:-1]:
            transformed = step.transform(transformed)
        lr = model.steps[-1][1]
        contributions = (lr.coef_[0] * transformed[0]).astype(float)
        base = float(lr.intercept_[0])
        return contributions, base, base + float(contributions.sum()), "log-odds"

    import shap  # imported here: it is slow to import and only needed for explanations

    explainer = shap.TreeExplainer(model)
    values = explainer.shap_values(row)
    if isinstance(values, list):  # older shap: one array per class
        values = values[1]
    values = np.asarray(values)
    if values.ndim == 3:  # (rows, features, classes): take the illicit class
        values = values[..., 1]
    expected = np.atleast_1d(explainer.expected_value)
    base = float(expected[-1])
    contributions = values[0].astype(float)
    units = "log-odds" if family == "xgboost" else "probability"
    return contributions, base, base + float(contributions.sum()), units


def explain(model, family: str, row: pd.DataFrame, top: int = TOP_FEATURES) -> dict:
    """SHAP explanation of one prediction, biggest contributions first."""
    contributions, base, output, units = shap_values(model, family, row)
    order = np.argsort(-np.abs(contributions))
    shown = order[:top]
    rest = order[top:]
    features = []
    for i in shown:
        value = row.iloc[0, i]
        features.append({
            "feature": row.columns[i],
            "value": None if pd.isna(value) else float(value),
            "contribution": round(float(contributions[i]), 6),
            "pushes": "towards illicit" if contributions[i] > 0 else "towards licit",
        })
    probability = _sigmoid(output) if units == "log-odds" else output
    return {
        "method": "SHAP TreeExplainer" if family != "logistic_regression" else "SHAP (exact, linear model)",
        "units": units,
        "base_value": round(base, 6),
        "output": round(output, 6),
        "probability": round(float(probability), 6),
        "features": features,
        "other_features": {"count": int(len(rest)), "contribution": round(float(contributions[rest].sum()), 6)},
        "sum_check": round(float(base + contributions.sum() - output), 9),
        "note": ("Each bar is how much that feature moved this score away from the average score of the "
                 "training data. It explains the model, not the address: a large bar is not evidence of a crime."),
    }
