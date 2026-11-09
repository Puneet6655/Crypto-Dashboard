"""Inspect the Elliptic++ dataset and write a data card.

Run from the backend folder:
    python -m training.inspect_elliptic --data-dir data/raw/elliptic_pp --out reports/elliptic_pp_data_card.md

It reads the CSV files, counts rows and labels, checks for missing values,
checks how labels spread over time steps, measures how many wallets appear on
both sides of the paper's time split, and records which features could or
could not be rebuilt from live mempool.space data. It trains nothing.
"""
import argparse
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

CLASS_NAMES = {1: "illicit", 2: "licit", 3: "unknown"}
TRAIN_LAST_STEP = 34  # Elliptic++ paper: time steps 1-34 train, 35-49 test

WALLET_FILES = {"features": "wallets_features.csv", "classes": "wallets_classes.csv"}
TX_FILES = {"features": "txs_features.csv", "classes": "txs_classes.csv"}


def find_column(columns: list[str], candidates: list[str]) -> str:
    """Return the first column whose simplified name matches a candidate.

    "Time step", "time_step" and "TimeStep" all simplify to "timestep".
    Raises ValueError listing the real columns if nothing matches.
    """
    simple = {name.lower().replace(" ", "").replace("_", ""): name for name in columns}
    for candidate in candidates:
        if candidate in simple:
            return simple[candidate]
    raise ValueError(f"None of {candidates} found. Columns are: {columns[:15]}...")


def load_features(path: Path, id_candidates: list[str]) -> tuple[pd.DataFrame, str, str]:
    """Read a features CSV with numbers as float32 to halve memory use.

    Returns (table, id column name, time-step column name).
    """
    header = list(pd.read_csv(path, nrows=0).columns)
    id_col = find_column(header, id_candidates)
    step_col = find_column(header, ["timestep"])
    dtypes = {name: "float32" for name in header}
    dtypes[id_col] = "string"
    dtypes[step_col] = "int16"
    return pd.read_csv(path, dtype=dtypes), id_col, step_col


def load_classes(path: Path, id_candidates: list[str]) -> tuple[pd.DataFrame, str, str]:
    """Read a classes CSV. Returns (table, id column name, class column name)."""
    table = pd.read_csv(path, dtype="string")
    id_col = find_column(list(table.columns), id_candidates)
    class_col = find_column(list(table.columns), ["class", "label"])
    table[class_col] = pd.to_numeric(table[class_col]).astype("int8")
    return table, id_col, class_col


def label_counts(classes: pd.DataFrame, class_col: str) -> dict[str, int]:
    """Count each class by name, e.g. {'illicit': 14266, 'licit': ..., 'unknown': ...}."""
    counts = classes[class_col].map(CLASS_NAMES).value_counts()
    return {name: int(counts.get(name, 0)) for name in CLASS_NAMES.values()}


def live_compatibility(column: str) -> str:
    """Say whether a feature could be computed from mempool.space data, and the catch."""
    name = column.lower()
    if name.replace(" ", "").replace("_", "") == "timestep":
        return "No: Elliptic-specific time index"
    if name.startswith(("local_feature", "aggregate_feature", "lf_", "af_")):
        return "No: anonymised Elliptic feature"
    keywords = ("btc", "fee", "txs", "block", "lifetime", "timesteps", "transacted", "address", "size", "degree")
    if any(word in name for word in keywords):
        return "Concept yes, values no: Elliptic++ counts only inside its own subgraph and period"
    return "Unknown: check the paper's definition"


def summarise(name: str, features: pd.DataFrame, id_col: str, step_col: str,
              classes: pd.DataFrame, class_id_col: str, class_col: str) -> list[str]:
    """Return the markdown lines describing one dataset (wallets or transactions)."""
    lines = [f"## {name}", ""]
    feature_cols = [c for c in features.columns if c not in (id_col, step_col)]
    lines.append(f"- Feature rows: {len(features):,}; distinct IDs: {features[id_col].nunique():,}; feature columns: {len(feature_cols)}")
    lines.append(f"- Time steps: {features[step_col].min()} to {features[step_col].max()}")

    counts = label_counts(classes, class_col)
    lines.append(f"- Labels: {counts['illicit']:,} illicit, {counts['licit']:,} licit, {counts['unknown']:,} unknown")
    known = counts["illicit"] + counts["licit"]
    if known:
        lines.append(f"- Illicit share among known labels: {counts['illicit'] / known:.1%}")

    missing = features[feature_cols].isna().sum()
    missing = missing[missing > 0].sort_values(ascending=False)
    lines.append(f"- Columns with missing values: {len(missing)}" + (f" (worst: {missing.index[0]}, {int(missing.iloc[0]):,} rows)" if len(missing) else ""))
    constant = [c for c in feature_cols if features[c].nunique(dropna=True) <= 1]
    lines.append(f"- Constant columns (no information): {len(constant)}")

    unlabelled = ~features[id_col].isin(classes[class_id_col])
    lines.append(f"- Feature rows with no label row: {int(unlabelled.sum()):,}")

    joined = features[[id_col, step_col]].merge(
        classes[[class_id_col, class_col]], left_on=id_col, right_on=class_id_col, how="inner"
    )
    joined = joined[joined[class_col] != 3]
    by_step = joined.groupby(step_col)[class_col].agg(
        illicit=lambda s: int((s == 1).sum()), licit=lambda s: int((s == 2).sum())
    )
    lines += ["", "### Known labels per time step", "", "| Time step | Illicit | Licit |", "| --- | --- | --- |"]
    lines += [f"| {step} | {row.illicit:,} | {row.licit:,} |" for step, row in by_step.iterrows()]

    train_ids = set(features.loc[features[step_col] <= TRAIN_LAST_STEP, id_col])
    test_ids = set(features.loc[features[step_col] > TRAIN_LAST_STEP, id_col])
    overlap = len(train_ids & test_ids)
    lines += ["", f"- IDs appearing both in steps 1-{TRAIN_LAST_STEP} and later steps: {overlap:,}"
              + (" (leakage risk: split by ID as well as by time)" if overlap else "")]

    lines += ["", "### Can each feature be rebuilt from live mempool.space data?", "",
              "| Feature | Live compatibility |", "| --- | --- |"]
    lines += [f"| {c} | {live_compatibility(c)} |" for c in feature_cols]
    lines.append("")
    return lines


def build_card(data_dir: Path) -> str:
    """Inspect whichever Elliptic++ files are present and return the data card text."""
    lines = [
        "# Elliptic++ data card",
        "",
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC from `{data_dir}` by training/inspect_elliptic.py.",
        "",
        "Source: Elmougy and Liu (2023), KDD '23, https://github.com/git-disl/EllipticPlusPlus.",
        "Licence: none stated in the repository. Confirm permitted use with your supervisor; do not commit the CSV files.",
        "Label meaning: class 1 illicit, 2 licit, 3 unknown. Unknown is not licit and must not be used as a negative.",
        "",
    ]
    found = False
    for name, files, id_candidates in [
        ("Wallets (addresses)", WALLET_FILES, ["address"]),
        ("Transactions", TX_FILES, ["txid"]),
    ]:
        feature_path, class_path = data_dir / files["features"], data_dir / files["classes"]
        if not (feature_path.exists() and class_path.exists()):
            lines += [f"## {name}", "", f"Skipped: {files['features']} or {files['classes']} not found.", ""]
            continue
        found = True
        features, id_col, step_col = load_features(feature_path, id_candidates)
        classes, class_id_col, class_col = load_classes(class_path, id_candidates)
        lines += summarise(name, features, id_col, step_col, classes, class_id_col, class_col)
    if not found:
        raise FileNotFoundError(f"No Elliptic++ files found in {data_dir}")
    return "\n".join(lines)


def show_columns(data_dir: Path) -> None:
    """Print the real header of every CSV in data_dir, so the guessed names can be checked first."""
    csv_files = sorted(data_dir.glob("*.csv"))
    if not csv_files:
        raise FileNotFoundError(f"No CSV files found in {data_dir}")
    for path in csv_files:
        header = list(pd.read_csv(path, nrows=0).columns)
        preview = header if len(header) <= 20 else header[:20] + [f"... and {len(header) - 20} more"]
        print(f"{path.name}: {len(header)} columns")
        print("   ", preview)


def main() -> None:
    parser = argparse.ArgumentParser(description="Write a data card for Elliptic++")
    parser.add_argument("--data-dir", type=Path, default=Path("data/raw/elliptic_pp"))
    parser.add_argument("--out", type=Path, default=Path("reports/elliptic_pp_data_card.md"))
    parser.add_argument("--show-columns", action="store_true",
                        help="Only print each CSV's column names (fast). Run this first.")
    args = parser.parse_args()

    if args.show_columns:
        show_columns(args.data_dir)
        return

    card = build_card(args.data_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(card, encoding="utf-8")
    print(card[:1500])
    print(f"\nFull data card written to {args.out}")


if __name__ == "__main__":
    main()
